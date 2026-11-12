from datetime import timedelta

from test_app import client, auth, _make_discussion_with_model, SessionLocal, db
from test_native_harness import BRIEF
from planning import answer_question, save_question
from harness import facts_for


def seed(pid,did):
    with SessionLocal() as session:
        ids=[]
        for number in range(3):
            question,_=save_question(session,pid,did,{**BRIEF,'question':f'Câu hỏi kiểm thử số {number+1}?'})
            question.created_at=db.utcnow()+timedelta(seconds=number-10)
            answer_question(session,question,{'selected_ids':['o1'],'note':f'Ghi chú {number+1}'})
            ids.append(question.id)
        return ids


def test_reopen_preserves_both_answers_and_invalidates_later_branch(client):
    csrf=auth(client)
    pid,did=_make_discussion_with_model(client,csrf,'answer-history')
    ids=seed(pid,did)
    base=f'/api/projects/{pid}/planning/questions'
    with SessionLocal() as session:
        # Simulate an answer stored before history tracking existed.
        session.get(db.PlanningQuestion,ids[1]).answer_history=[]
        session.get(db.Project,pid).profile={'_discovery':{'status':'draft_ready'}}
        session.commit()
    assert client.post(f'{base}/{ids[1]}/reopen',json={}).status_code==403
    for expected in (2,4):
        reopened=client.post(f'{base}/{ids[1]}/reopen',headers=csrf,json={})
        assert reopened.status_code==200,reopened.text
        assert len(reopened.json()['question']['answer_history'])==expected
        again=client.post(f'{base}/{ids[1]}/reopen',headers=csrf,json={})
        assert again.status_code==409
        answer=client.post(f'{base}/{ids[1]}/answer',headers=csrf,json={'selected_ids':['o2'],'note':f'Đổi lần {expected}'})
        assert answer.status_code==200,answer.text
    with SessionLocal() as session:
        questions=[session.get(db.PlanningQuestion,key) for key in ids]
        assert [q.status for q in questions]==['answered','answered','superseded']
        assert len(questions[1].answer_history)==5
        assert questions[1].answer_history[0]['answer']['selected_ids']==['o1']
        assert questions[1].answer['selected_ids']==['o2']
        assert questions[2].answer=={}
        assert questions[2].answer_history[0]['answer']['note']=='Ghi chú 3'
        assert session.query(db.Message).filter_by(discussion_id=did,role='user').count()==5
        project=session.get(db.Project,pid)
        assert project.profile['_discovery']['status']=='interviewing'
        facts=facts_for(session,project,did)
        assert len(facts['questions'])==2
        assert facts['questions'][1]['choice']==[BRIEF['options'][1]['label']]
    history=client.get(base,params={'discussion_id':did}).json()['history']
    assert len(history)==3
    assert next(q for q in history if q['id']==ids[1])['options'][0]['pros']
    other,_=_make_discussion_with_model(client,csrf,'other-history')
    assert client.post(f'/api/projects/{other}/planning/questions/{ids[1]}/reopen',headers=csrf,json={}).status_code==404


def test_reopen_is_atomic_while_a_worker_owns_the_project(client):
    csrf=auth(client)
    pid,did=_make_discussion_with_model(client,csrf,'busy-history')
    ids=seed(pid,did)
    with SessionLocal() as session:
        run=db.AgentRun(project_id=pid,discussion_id=did,status='running')
        session.add(run);session.commit();run_id=run.id
    response=client.post(f'/api/projects/{pid}/planning/questions/{ids[0]}/reopen',headers=csrf,json={})
    assert response.status_code==409
    with SessionLocal() as session:
        assert all(session.get(db.PlanningQuestion,key).status=='answered' for key in ids)
        session.get(db.AgentRun,run_id).status='completed';session.commit()
    response=client.post(f'/api/projects/{pid}/planning/questions/{ids[0]}/reopen',headers=csrf,json={})
    assert response.status_code==200
    pending=client.get(f'/api/projects/{pid}/planning/questions',params={'discussion_id':did}).json()['pending']
    assert pending['id']==ids[0] and pending['answer']=={}

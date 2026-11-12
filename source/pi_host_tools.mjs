/** Pure Pi SDK tool definitions. The host NEVER exposes Pi's shell or filesystem tools.
 * Every call crosses FastAPI's project-scoped, run-specific capability gateway.
 */
import { Type } from 'typebox';
import { defineTool } from '@earendil-works/pi-coding-agent';

const short = max => Type.String({minLength:1,maxLength:max});
const optional = max => Type.Optional(Type.String({maxLength:max}));
export const TOOL_DEFS = [
  ['search_web','Search SearXNG and record public sources',Type.Object({query:short(300)})],
  ['fetch_url','Retrieve a public page, never private/LAN endpoints',Type.Object({url:short(2000),use_browser:Type.Optional(Type.Boolean())})],
  ['list_project_files','List allowed project folders',Type.Object({folder:optional(300)})],
  ['read_project_file','Read allowed text files in the active project',Type.Object({path:short(400)})],
  ['write_document','Write DRAFT Markdown only, never approve it',Type.Object({filename:short(120),content:short(50000)})],
  ['propose_decision','Propose an unapproved decision',Type.Object({title:short(250),description:short(6000),rationale:optional(4000)})],
  ['propose_requirement','Propose an unapproved requirement',Type.Object({title:short(250),description:short(6000),criteria:optional(4000),priority:optional(20)})],
  ['propose_memory','Propose memory for human review',Type.Object({summary:short(2400)})],
  ['propose_planning_question','Propose a guided choice with human approval',Type.Object({
    question:short(2000),context:optional(6000),
    options:Type.Array(Type.Object({label:short(150),description:short(2000),pros:short(1600),cons:short(1600)}),{minItems:2,maxItems:4}),
    recommended_index:Type.Integer({minimum:0,maximum:3}),recommendation_reason:short(3000),impact:optional(3000),
    assumptions:Type.Optional(Type.Array(Type.String({maxLength:1000}),{maxItems:12})),
    allow_multiple:Type.Optional(Type.Boolean()),sources:Type.Optional(Type.Array(Type.String({maxLength:500}),{maxItems:10}))
  })]
];

export function createScopedTools(active) {
  return TOOL_DEFS.map(([name,description,parameters]) => defineTool({
    name,label:name.replaceAll('_',' '),description,parameters,
    async execute(_id,params,signal) {
      // No global environment variables or model-supplied IDs are used as authority.
      const cap=active.current;
      if (!cap?.runId || !cap?.token) {
        return {content:[{type:'text',text:'Tool is not authorized for an active run'}],details:{error:true}};
      }
      try {
        const response=await fetch(process.env.PI_TOOL_URL || 'http://web:8080/api/internal/pi-tool',{
          method:'POST',headers:{'Content-Type':'application/json','X-Specweave-Run':cap.runId,'X-Specweave-Token':cap.token},
          body:JSON.stringify({name,args:params}),signal,
        });
        if (!response.ok) return {content:[{type:'text',text:`SpecWeave rejected tool (${response.status})`}],details:{error:true}};
        const result=await response.json();
        const text=JSON.stringify(result);
        return {content:[{type:'text',text:text.length>19000?text.slice(0,19000)+' [truncated]':text}],details:{ok:true}};
      } catch (err) {
        return {content:[{type:'text',text:`Tool request failed (${err?.name||'unknown'})`}],details:{error:true}};
      }
    }
  }));
}

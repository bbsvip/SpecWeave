/**
 * SpecWeave's only permitted Pi tools. Native filesystem, shell and MCP tools
 * remain disabled by the runner. The API validates the run capability and
 * project scope; the LLM cannot set project/discussion/run IDs here.
 */
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { Type } from "typebox";

const string = (max: number) => Type.String({ minLength: 1, maxLength: max });
const OPTIONAL = (max: number) => Type.Optional(Type.String({ maxLength: max }));
const specs = [
  { name: "search_web", description: "Search public web sources via configured SearXNG.", schema: Type.Object({ query: string(300) }) },
  { name: "fetch_url", description: "Fetch a public HTTP(S) page. Private networks are denied by SpecWeave.", schema: Type.Object({ url: string(2000), use_browser: Type.Optional(Type.Boolean()) }) },
  { name: "list_project_files", description: "List files in allowed folders of the current project.", schema: Type.Object({ folder: OPTIONAL(300) }) },
  { name: "read_project_file", description: "Read UTF-8 text in the current project, never secrets or system files.", schema: Type.Object({ path: string(400) }) },
  { name: "write_document", description: "Create or modify a DRAFT Markdown in docs/drafts only. Never approve it.", schema: Type.Object({ filename: string(120), content: string(50000) }) },
  { name: "propose_decision", description: "Record an unapproved decision proposal in the current project.", schema: Type.Object({ title: string(250), description: string(6000), rationale: OPTIONAL(4000) }) },
  { name: "propose_requirement", description: "Record a draft requirement with acceptance criteria.", schema: Type.Object({ title: string(250), description: string(6000), criteria: OPTIONAL(4000), priority: OPTIONAL(20) }) },
  { name: "propose_memory", description: "Propose project-specific memory for later human review.", schema: Type.Object({ summary: string(2400) }) },
  { name: "propose_planning_question", description: "Create one guided planning decision brief with recommended choice; options require pros and cons.", schema: Type.Object({
    question: string(2000), context: OPTIONAL(6000),
    options: Type.Array(Type.Object({label:string(150),description:string(2000),pros:string(1600),cons:string(1600)}),{minItems:2,maxItems:4}),
    recommended_index:Type.Integer({minimum:0,maximum:3}),recommendation_reason:string(3000),
    impact:OPTIONAL(3000),assumptions:Type.Optional(Type.Array(Type.String({maxLength:1000}),{maxItems:12})),
    allow_multiple:Type.Optional(Type.Boolean()),sources:Type.Optional(Type.Array(Type.String({maxLength:500}),{maxItems:10})),
  }) },
] as const;

export default function(pi: ExtensionAPI) {
  for(const spec of specs) {
    pi.registerTool({
      name: spec.name,
      label: spec.name.replaceAll("_", " "),
      description: spec.description,
      parameters: spec.schema,
      async execute(_toolCallId, params, signal) {
        const url = process.env.SPECWEAVE_TOOL_URL;
        const token = process.env.SPECWEAVE_TOOL_TOKEN;
        const run = process.env.SPECWEAVE_TOOL_RUN;
        if (!url || !token || !run) {
          return {content:[{type:"text",text:"Tool bridge is not configured"}],details:{error:true}};
        }
        try {
          const response = await fetch(url, {
            method: "POST", headers: {"Content-Type":"application/json", "X-Specweave-Run":run, "X-Specweave-Token":token},
            body: JSON.stringify({name:spec.name,args:params}), signal,
          });
          if(!response.ok) {
            return {content:[{type:"text",text:`Tool rejected by backend (HTTP ${response.status})`}],details:{error:true}};
          }
          const result = await response.json();
          const text = JSON.stringify(result);
          return {content:[{type:"text",text:text.length>19000?text.slice(0,19000)+" [truncated]":text}],details:{ok:true}};
        } catch(err) {
          return {content:[{type:"text",text:`Tool transport failed (${err instanceof Error ? err.name : "unknown"})`}],details:{error:true}};
        }
      }
    });
  }
}

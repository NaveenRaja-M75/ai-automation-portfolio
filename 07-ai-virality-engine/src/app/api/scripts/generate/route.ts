import { NextResponse } from "next/server";
import { 
  compileContext, 
  runResearchAgent, 
  runHookSpecialist, 
  runNarrativeArchitect, 
  runScriptWriter, 
  runRetentionCritic 
} from "@/utils/ai/agents";

export async function POST(req: Request) {
  try {
    const body = await req.json();
    
    // Agent 0: Context Compiler
    const context = await compileContext(body);
    
    // Agent 1: Research Agent
    const research = await runResearchAgent(context);
    
    // Agent 2: Hook Specialist
    const hooks = await runHookSpecialist(context, research);
    
    // Agent 3: Narrative Architect
    const narrative = await runNarrativeArchitect(context, research, hooks);
    
    // Agent 4: Script Writer
    const draftScript = await runScriptWriter(context, research, hooks, narrative);
    
    // Agent 5: Retention Critic
    const finalScript = await runRetentionCritic(draftScript);
    
    return NextResponse.json({
      success: true,
      data: {
        context,
        research,
        hooks,
        narrative,
        draftScript,
        finalScript
      }
    });
    
  } catch (error: any) {
    console.error("AI Generation Error:", error);
    return NextResponse.json({ success: false, error: error.message }, { status: 500 });
  }
}

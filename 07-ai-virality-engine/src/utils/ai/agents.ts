import { generateJSON } from "./gemini";

// AGENT 0: Context Compiler
export async function compileContext(inputs: { niche: string, topic: string, targetAudience: string, tone: string, pacing: string }) {
  const prompt = `
    You are the Context Compiler, an elite YouTube strategist.
    Your job is to build the foundational psychological profile for a high-retention video.
    
    Inputs:
    - Niche: ${inputs.niche}
    - Topic: ${inputs.topic}
    - Audience: ${inputs.targetAudience}
    - Tone: ${inputs.tone}
    - Pacing: ${inputs.pacing}
    
    CRITICAL RULES:
    - NEVER allow generic YouTube concepts ("Welcome back", "Today we will...").
    - Define the core tension (Status Quo vs. The Truth).
    
    Output a JSON object exactly like this:
    {
      "coreTension": "The main conflict or mystery the video resolves",
      "audiencePsychology": "What the audience secretly fears or desires regarding this topic",
      "storytellingStyle": "The exact narrative framework (e.g., 'Mystery Box', 'Hero's Journey', 'Expose')",
      "emotionalBaseline": "The primary emotion to evoke (e.g., 'Dread', 'Awe', 'Urgency')",
      "pacingRules": "Rules for sentence length and visual cuts based on the requested pacing",
      "forbiddenWords": ["delve", "explore", "fascinating", "dive into", "tapestry", "in today's video"]
    }
  `;
  return await generateJSON(prompt);
}

// AGENT 1: Research Agent
export async function runResearchAgent(context: any) {
  const prompt = `
    You are the Research Analyst. 
    You do not just find facts; you find the *emotional weight* behind the facts.
    
    Context: ${JSON.stringify(context, null, 2)}
    
    CRITICAL RULES:
    - Identify the "Pattern Interrupt" moments—where the audience's expectation can be shattered.
    - Identify the highest risk of boredom (where the topic gets too technical or slow).
    
    Output a JSON object exactly like this:
    {
      "emotionalDrivers": ["Specific emotion 1", "Specific emotion 2"],
      "curiosityGaps": ["What question must the viewer stay to answer?"],
      "statusQuoVsTruth": "What people believe vs what the video reveals",
      "boredomRisks": ["Where viewers will click off and how to prevent it"]
    }
  `;
  return await generateJSON(prompt);
}

// AGENT 2: Hook Specialist
export async function runHookSpecialist(context: any, research: any) {
  const prompt = `
    You are an elite Hook Copywriter. Your only job is to stop a viewer from scrolling in the first 5 seconds.
    
    Context: ${JSON.stringify(context)}
    Research: ${JSON.stringify(research)}
    
    CRITICAL RULES:
    - No rhetorical questions starting with "Have you ever wondered..." or "What if I told you...".
    - No introductions. Start In Media Res (in the middle of the action).
    - Use visceral, punchy language.
    - Keep hooks under 35 words.
    
    Output a JSON object exactly like this:
    {
      "hooks": [
        { 
          "style": "The Paradox", 
          "hookText": "Exact narration script", 
          "psychologicalTrigger": "Why this works" 
        },
        { 
          "style": "The Bold Claim", 
          "hookText": "Exact narration script", 
          "psychologicalTrigger": "Why this works" 
        },
        { 
          "style": "The Visual Mystery", 
          "hookText": "Exact narration script", 
          "psychologicalTrigger": "Why this works" 
        }
      ]
    }
  `;
  return await generateJSON(prompt);
}

// AGENT 3: Narrative Architect
export async function runNarrativeArchitect(context: any, research: any, hooks: any) {
  const prompt = `
    You are the Narrative Architect. You design the retention structure of the video.
    
    Context: ${JSON.stringify(context)}
    Research: ${JSON.stringify(research)}
    Selected Hook Idea: ${JSON.stringify(hooks.hooks[0])}
    
    CRITICAL RULES:
    - Videos must have a multi-act structure.
    - Every 45-60 seconds MUST have a "Pattern Interrupt" (a shift in tone, a new mystery, a sudden visual change).
    - Endings must be abrupt and impactful, no long goodbyes.
    
    Output a JSON object exactly like this:
    {
      "pacingMap": [
        { "phase": "0:00-0:30 (The Hook)", "goal": "Establish the mystery", "emotion": "..." },
        { "phase": "0:30-2:00 (The Setup)", "goal": "Raise the stakes", "emotion": "..." },
        { "phase": "2:00-5:00 (The Escalation)", "goal": "Introduce the twist", "emotion": "..." },
        { "phase": "5:00+ (The Payoff)", "goal": "Deliver the truth rapidly", "emotion": "..." }
      ],
      "patternInterrupts": ["List 3 specific moments to change the rhythm"]
    }
  `;
  return await generateJSON(prompt);
}

// AGENT 4: Script Writer
export async function runScriptWriter(context: any, research: any, hooks: any, narrative: any) {
  const prompt = `
    You are an elite Cinematic Scriptwriter. 
    You are writing the exact voiceover narration and visual cues.
    
    Context: ${JSON.stringify(context)}
    Research: ${JSON.stringify(research)}
    Selected Hook Idea: ${JSON.stringify(hooks.hooks[0])}
    Narrative Flow: ${JSON.stringify(narrative)}
    
    CRITICAL RULES:
    - Write exactly how a human speaks. Use short, punchy sentences. Occasional fragments for dramatic effect.
    - DO NOT USE THESE WORDS: ${JSON.stringify(context.forbiddenWords)}.
    - No robotic transitions like "Firstly", "In conclusion", "Another point is".
    - The visuals MUST drive the story as much as the words.
    
    Output a JSON object exactly like this:
    {
      "titleIdeas": ["Viral Title 1", "Viral Title 2"],
      "sections": [
        { 
          "sectionName": "The Hook", 
          "narration": "Exact spoken words", 
          "visualDirection": "Detailed cinematic visual cue" 
        },
        { 
          "sectionName": "The Setup", 
          "narration": "...", 
          "visualDirection": "..." 
        }
      ]
    }
  `;
  return await generateJSON(prompt);
}

// AGENT 5: Retention Critic
export async function runRetentionCritic(script: any) {
  const prompt = `
    You are the aggressive Retention Editor.
    Your job is to review the draft script and cut ALL generic AI fluff, repetitive phrasing, and boring pacing.
    
    Draft Script: ${JSON.stringify(script)}
    
    CRITICAL RULES:
    - Delete ANY sentence that doesn't advance the plot or provide emotional weight.
    - Ensure the hook is punchy.
    - If it sounds like AI wrote it, rewrite it to sound gritty, human, and cinematic.
    
    Output a JSON object exactly like this:
    {
      "retentionImprovementsMade": ["What you fixed and why"],
      "finalTitle": "The single best title",
      "finalSections": [
        { 
          "sectionName": "string", 
          "narration": "Polished, aggressive, human-sounding script", 
          "visualDirection": "Enhanced cinematic visual", 
          "bRollSuggestion": "Specific stock footage or motion graphic idea" 
        }
      ],
      "thumbnailConcepts": ["Visual idea 1", "Visual idea 2"]
    }
  `;
  return await generateJSON(prompt);
}

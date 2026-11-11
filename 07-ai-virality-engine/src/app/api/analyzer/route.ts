import { NextResponse } from "next/server";
import { generateJSON } from "@/utils/ai/gemini";

export async function POST(req: Request) {
  try {
    const { url } = await req.json();
    
    const prompt = `
      You are an elite YouTube Channel Strategist and Retention Psychology Engine.
      Analyze the hypothetical content strategy of a channel operating at this URL or in this niche: ${url}
      
      CRITICAL INSTRUCTIONS:
      Do not give generic advice ("make better thumbnails", "post more"). 
      Provide ruthless, structural analysis of retention mechanics, psychological hooks, and emotional pacing.
      
      Output a structured JSON object exactly like this:
      {
        "scores": {
          "hookCuriosity": 85,
          "emotionalIntensity": 72,
          "retentionRisk": 40,
          "narrativeStrength": 90
        },
        "viralPatterns": [
          "Detailed observation of a specific psychological pattern that holds viewers",
          "Another deep retention mechanic used by this niche"
        ],
        "weaknesses": [
          "Specific structural flaw (e.g., 'The B-story is introduced too late, causing a mid-roll dip')",
          "Another ruthless critique of pacing or tone"
        ]
      }
    `;

    const data = await generateJSON(prompt);
    
    return NextResponse.json({ success: true, data });
  } catch (error: any) {
    return NextResponse.json({ success: false, error: error.message }, { status: 500 });
  }
}

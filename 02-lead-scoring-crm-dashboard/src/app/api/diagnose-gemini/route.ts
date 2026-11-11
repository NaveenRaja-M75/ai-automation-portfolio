import { NextResponse } from 'next/server';
import { GoogleGenerativeAI } from '@google/generative-ai';

export async function GET() {
  const apiKey = process.env.GEMINI_API_KEY || '';
  
  if (!apiKey || apiKey === 'your_gemini_api_key') {
    return NextResponse.json({
      success: false,
      error: 'GEMINI_API_KEY is not configured in .env.local.',
    }, { status: 400 });
  }

  const results: { model: string; success: boolean; response?: string; error?: string }[] = [];
  let availableModels: string[] = [];

  try {
    // 1. Fetch available models from Google API
    const listRes = await fetch(`https://generativelanguage.googleapis.com/v1beta/models?key=${apiKey}`);
    const data = await listRes.json();
    
    if (data.models) {
      availableModels = data.models.map((m: any) => m.name.replace('models/', ''));
    } else {
      return NextResponse.json({
        success: false,
        error: 'Failed to fetch models list from Google API.',
        rawResponse: data
      }, { status: 500 });
    }
  } catch (err: any) {
    return NextResponse.json({
      success: false,
      error: 'Failed to list models: ' + err.message,
    }, { status: 500 });
  }

  // 2. Filter text generation models to test
  const modelsToTest = availableModels.filter(m => 
    m.includes('gemini') && 
    !m.includes('embedding') && 
    !m.includes('robotics') && 
    !m.includes('preview-tts')
  );

  const genAI = new GoogleGenerativeAI(apiKey);

  console.log(`[Diagnostics] Testing ${modelsToTest.length} Gemini models...`);

  for (const modelName of modelsToTest) {
    try {
      console.log(`[Diagnostics] Testing model "${modelName}"...`);
      const model = genAI.getGenerativeModel({ model: modelName });
      const result = await model.generateContent('Respond with exactly "SUCCESS".');
      const text = result.response.text().trim();
      results.push({
        model: modelName,
        success: true,
        response: text
      });
      console.log(`[Diagnostics] ✅ Model "${modelName}" succeeded: "${text}"`);
    } catch (err: any) {
      results.push({
        model: modelName,
        success: false,
        error: err.message
      });
      console.log(`[Diagnostics] ❌ Model "${modelName}" failed: ${err.message}`);
    }
  }

  return NextResponse.json({
    success: true,
    testedModelsCount: modelsToTest.length,
    results,
    allAvailableModels: availableModels
  });
}

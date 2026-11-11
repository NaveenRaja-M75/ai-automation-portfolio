import { NextResponse } from 'next/server';
import { GoogleGenerativeAI } from '@google/generative-ai';

export async function GET() {
  const apiKey = process.env.GEMINI_API_KEY || '';
  
  if (!apiKey || apiKey === 'your_gemini_api_key') {
    return NextResponse.json({
      success: false,
      error: 'GEMINI_API_KEY is missing or set to placeholder value in .env.local.',
      keyDetected: false,
    }, { status: 400 });
  }

  try {
    const genAI = new GoogleGenerativeAI(apiKey);
    const model = genAI.getGenerativeModel({ model: 'gemini-2.5-flash' });
    
    console.log('[Test Gemini Route] Dispatching test request to Google Gemini API...');
    const result = await model.generateContent('Respond with exactly "SUCCESS".');
    const text = result.response.text().trim();

    return NextResponse.json({
      success: true,
      rawResponse: text,
      keyDetected: true,
      apiKeyMasked: `${apiKey.slice(0, 4)}...${apiKey.slice(-4)}`,
    });
  } catch (error: any) {
    console.error('[Test Gemini Route] API call failed:', error);
    return NextResponse.json({
      success: false,
      error: error.message || 'Gemini API call failed.',
      keyDetected: true,
    }, { status: 500 });
  }
}

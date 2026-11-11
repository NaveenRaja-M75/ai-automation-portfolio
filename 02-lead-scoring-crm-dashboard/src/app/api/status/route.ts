import { NextResponse } from 'next/server';
import { isMockMode, supabase } from '@/lib/db';
import { isGeminiActive } from '@/lib/gemini';

export async function GET() {
  try {
    const isDbMock = isMockMode();
    const isAiActive = isGeminiActive();
    
    let supabaseConnected = false;
    let dbDetails = 'Running in Sandbox Mode (Memory)';
    let errorDetails = null;

    if (!isDbMock && supabase) {
      try {
        // Query the leads table to verify we have read/write access
        const { data, error } = await supabase
          .from('leads')
          .select('id')
          .limit(1);

        if (error) {
          errorDetails = error.message;
          console.error('[Status API] Supabase query failed:', error);
        } else {
          supabaseConnected = true;
          dbDetails = 'Supabase Cloud Database';
        }
      } catch (dbErr: any) {
        errorDetails = dbErr.message;
        console.error('[Status API] Supabase connection error:', dbErr);
      }
    }

    const currentMode = (!isDbMock && isAiActive) ? 'Production' : 'Sandbox';

    return NextResponse.json({
      supabaseConnected,
      geminiConnected: isAiActive,
      currentMode,
      databaseType: dbDetails,
      geminiStatus: isAiActive ? 'Active (API)' : 'Inactive (Mock Fallback)',
      envDetails: {
        hasSupabaseUrl: !!process.env.NEXT_PUBLIC_SUPABASE_URL,
        hasSupabaseKey: !!process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY,
        hasGeminiKey: !!process.env.GEMINI_API_KEY && process.env.GEMINI_API_KEY !== 'your_gemini_api_key'
      },
      error: errorDetails
    });
  } catch (error: any) {
    console.error('[Status API] Critical error in status check:', error);
    return NextResponse.json({ error: error.message }, { status: 500 });
  }
}

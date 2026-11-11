import { NextResponse } from 'next/server';
import { resetDatabase } from '@/lib/db';
import { MOCK_LEADS, MOCK_MESSAGES, MOCK_ACTIVITIES } from '@/lib/mockData';

export async function POST() {
  try {
    console.log('[Demo Reset API] Triggering database reset to default mock data...');
    await resetDatabase();
    console.log('[Demo Reset API] Database successfully reset.');
    return NextResponse.json({
      success: true,
      counts: {
        leads: MOCK_LEADS.length,
        messages: MOCK_MESSAGES.length,
        activities: MOCK_ACTIVITIES.length
      }
    });
  } catch (error: unknown) {
    console.error('Error resetting database:', error);
    const message = error instanceof Error ? error.message : 'Unknown error';
    return NextResponse.json({ error: message }, { status: 500 });
  }
}

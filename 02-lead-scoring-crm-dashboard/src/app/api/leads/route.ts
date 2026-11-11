import { NextRequest, NextResponse } from 'next/server';
import { getLeads, createLead, addActivity } from '@/lib/db';
import { LeadSource } from '@/types';

// GET /api/leads - Retrieve all leads
export async function GET() {
  try {
    const leads = await getLeads();
    return NextResponse.json({ leads });
  } catch (error: unknown) {
    console.error('Error fetching leads:', error);
    const message = error instanceof Error ? error.message : 'Unknown error';
    return NextResponse.json({ error: message }, { status: 500 });
  }
}

// POST /api/leads - Manually create a new lead (e.g. to test different sources)
export async function POST(req: NextRequest) {
  try {
    const body = await req.json();
    const { name, phone, leadSource = 'Website' } = body;

    if (!phone) {
      return NextResponse.json({ error: 'Phone number is required' }, { status: 400 });
    }

    const lead = await createLead({
      name: name || null,
      phone,
      budget: null,
      preferred_area: null,
      property_type: null,
      timeline: null,
      score: 10, // Default starting engagement score
      status: 'Cold',
      lead_source: leadSource as LeadSource,
      lead_stage: 'New',
      needs_human_review: false,
    });

    // Log Activity
    await addActivity(lead.id, 'lead_created', `Lead record created manually via Dashboard. Source set to ${leadSource}.`);

    return NextResponse.json({ lead });
  } catch (error: unknown) {
    console.error('Error creating lead:', error);
    const message = error instanceof Error ? error.message : 'Unknown error';
    return NextResponse.json({ error: message }, { status: 500 });
  }
}

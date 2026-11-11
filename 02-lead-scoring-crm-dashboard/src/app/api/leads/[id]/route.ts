import { NextRequest, NextResponse } from 'next/server';
import { getLeadById, getMessages, getActivities, updateLead, addActivity } from '@/lib/db';

// GET /api/leads/[id] - Fetch messages & activities for a selected lead
export async function GET(
  req: NextRequest,
  { params }: { params: Promise<{ id: string }> }
) {
  try {
    const { id } = await params;
    const lead = await getLeadById(id);
    
    if (!lead) {
      return NextResponse.json({ error: 'Lead not found' }, { status: 404 });
    }

    const messages = await getMessages(id);
    const activities = await getActivities(id);

    return NextResponse.json({
      lead,
      messages,
      activities
    });
  } catch (error: unknown) {
    console.error('Error fetching lead details:', error);
    const message = error instanceof Error ? error.message : 'Unknown error';
    return NextResponse.json({ error: message }, { status: 500 });
  }
}

// PATCH /api/leads/[id] - Update lead details (stage, human review, details)
export async function PATCH(
  req: NextRequest,
  { params }: { params: Promise<{ id: string }> }
) {
  try {
    const { id } = await params;
    const body = await req.json();
    const { lead_stage, needs_human_review, name, budget, preferred_area, property_type, timeline } = body;

    const existingLead = await getLeadById(id);
    if (!existingLead) {
      return NextResponse.json({ error: 'Lead not found' }, { status: 404 });
    }

    const updates: Record<string, string | boolean | number | null> = {};
    const auditLogs: string[] = [];

    // Check for stage updates
    if (lead_stage !== undefined && lead_stage !== existingLead.lead_stage) {
      updates.lead_stage = lead_stage;
      auditLogs.push(`Stage manually changed from "${existingLead.lead_stage}" to "${lead_stage}"`);
    }

    // Check for human review status updates
    if (needs_human_review !== undefined && needs_human_review !== existingLead.needs_human_review) {
      updates.needs_human_review = needs_human_review;
      if (needs_human_review === true) {
        auditLogs.push('Manually flagged for human review');
      } else {
        auditLogs.push('Human review request resolved/cleared');
      }
    }

    // Check for requirement edits
    if (name !== undefined && name !== existingLead.name) {
      updates.name = name;
      auditLogs.push(`Name updated to "${name}"`);
    }
    if (budget !== undefined && budget !== existingLead.budget) {
      updates.budget = budget;
      auditLogs.push(`Budget updated to "${budget}"`);
    }
    if (preferred_area !== undefined && preferred_area !== existingLead.preferred_area) {
      updates.preferred_area = preferred_area;
      auditLogs.push(`Area updated to "${preferred_area}"`);
    }
    if (property_type !== undefined && property_type !== existingLead.property_type) {
      updates.property_type = property_type;
      auditLogs.push(`Property Type updated to "${property_type}"`);
    }
    if (timeline !== undefined && timeline !== existingLead.timeline) {
      updates.timeline = timeline;
      auditLogs.push(`Timeline updated to "${timeline}"`);
    }

    // If no changes, return early
    if (Object.keys(updates).length === 0) {
      return NextResponse.json({ lead: existingLead });
    }

    // Apply updates
    const updatedLead = await updateLead(id, updates);

    // Save audit logs to activities
    for (const log of auditLogs) {
      const type = updates.needs_human_review === true 
        ? 'human_review_requested'
        : 'lead_updated';
      await addActivity(id, type, log);
    }

    // Fetch fresh activities list
    const activities = await getActivities(id);

    return NextResponse.json({
      lead: updatedLead,
      activities
    });

  } catch (error: unknown) {
    console.error('Error updating lead details:', error);
    const message = error instanceof Error ? error.message : 'Unknown error';
    return NextResponse.json({ error: message }, { status: 500 });
  }
}

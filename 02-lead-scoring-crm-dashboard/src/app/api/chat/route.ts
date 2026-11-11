import { NextRequest, NextResponse } from 'next/server';
import { getLeads, getLeadById, createLead, updateLead, getMessages, addMessage, addActivity } from '@/lib/db';
import { runAIQualification } from '@/lib/gemini';
import { calculateLeadScore } from '@/lib/scoring';
import { LeadSource, Lead } from '@/types';

// Deterministic Human Review Triggers
const TRIGGERS = [
  {
    type: 'Callback Request',
    keywords: ['callback', 'call me', 'phone call', 'mobile call', 'dial'],
    details: 'Customer requested a direct call back.'
  },
  {
    type: 'Human Representative Request',
    keywords: ['speak to', 'talk to', 'chat with', 'human', 'agent', 'salesperson', 'representative', 'broker', 'person', 'live person', 'someone'],
    details: 'Customer requested to talk with a real person.'
  },
  {
    type: 'Legal/Tax Inquiry',
    keywords: ['legal', 'tax', 'registration', 'stamp duty', 'court', 'deed', 'lawyer', 'documents'],
    details: 'Customer asked about legal documents, registration, or taxes.'
  },
  {
    type: 'Pricing Negotiation',
    keywords: ['discount', 'negotiate', 'negotiation', 'cheaper', 'reduce price', 'best price', 'offer', 'price reduce'],
    details: 'Customer requested price negotiations or discounts.'
  }
];

export async function POST(req: NextRequest) {
  try {
    const body = await req.json();
    const { leadId, phone, message, leadSource = 'WhatsApp' } = body;

    if (!phone || !message) {
      return NextResponse.json({ error: 'Phone and message are required' }, { status: 400 });
    }

    const now = new Date();
    let lead = null;

    // 1. Get or Create Lead
    if (leadId) {
      lead = await getLeadById(leadId);
    }
    
    if (!lead) {
      const allLeads = await getLeads(); // Fetch all to find match
      lead = allLeads.find((l: Lead) => l.phone === phone) || null;
    }

    if (!lead) {
      lead = await createLead({
        name: null,
        phone,
        budget: null,
        preferred_area: null,
        property_type: null,
        timeline: null,
        score: 0,
        status: 'Cold',
        lead_source: leadSource as LeadSource,
        lead_stage: 'New',
        needs_human_review: false,
      });

      // Log Lead Created activity
      await addActivity(lead.id, 'lead_created', `Lead record created automatically via ${leadSource}.`);
    }

    // 2. Save User Message
    const userMsg = await addMessage(lead.id, 'lead', message);

    // 3. Scan for Deterministic Human Review Triggers
    const lowerMessage = message.toLowerCase();
    let triggerMatched = null;
    for (const trig of TRIGGERS) {
      const match = trig.keywords.some(kw => lowerMessage.includes(kw));
      if (match) {
        triggerMatched = trig;
        break;
      }
    }

    let needsHumanReviewUpdate = lead.needs_human_review;
    if (triggerMatched && !lead.needs_human_review) {
      needsHumanReviewUpdate = true;
      await addActivity(
        lead.id,
        'human_review_requested',
        `Flagged for human review. Triggered by keyword in message: "${message}". Category: ${triggerMatched.type}.`
      );
    }

    // 4. Retrieve Full Message History for Gemini Context
    const messages = await getMessages(lead.id);
    // Convert DB messages to format expected by AI module
    const history = messages.map(m => ({
      sender: m.sender,
      content: m.content
    }));

    // 5. Run AI Qualification Engine (Gemini / Mock)
    const currentData = {
      name: lead.name,
      budget: lead.budget,
      preferred_area: lead.preferred_area,
      property_type: lead.property_type,
      timeline: lead.timeline,
    };
    
    const extraction = await runAIQualification(history, currentData);

    // 6. Calculate Score & Status (Deterministic rules)
    const userMsgCount = messages.filter(m => m.sender === 'lead').length;
    const scoringResult = calculateLeadScore({
      budget: extraction.budget,
      timeline: extraction.timeline,
      preferred_area: extraction.preferred_area,
      property_type: extraction.property_type,
    }, userMsgCount);

    // 7. Track Field Updates & Log Activities
    const updatedFields: string[] = [];
    if (extraction.name && extraction.name !== lead.name) {
      updatedFields.push(`Name updated to "${extraction.name}"`);
    }
    if (extraction.budget && extraction.budget !== lead.budget) {
      updatedFields.push(`Budget updated to "${extraction.budget}"`);
    }
    if (extraction.preferred_area && extraction.preferred_area !== lead.preferred_area) {
      updatedFields.push(`Area updated to "${extraction.preferred_area}"`);
    }
    if (extraction.property_type && extraction.property_type !== lead.property_type) {
      updatedFields.push(`Property Type updated to "${extraction.property_type}"`);
    }
    if (extraction.timeline && extraction.timeline !== lead.timeline) {
      updatedFields.push(`Timeline updated to "${extraction.timeline}"`);
    }

    if (updatedFields.length > 0) {
      await addActivity(lead.id, 'lead_updated', `Lead requirements updated: ${updatedFields.join(', ')}.`);
    }

    // Determine Stage Changes
    let newStage = lead.lead_stage;
    // Auto qualify if score >= 75 and stage is 'New'
    if (scoringResult.score >= 75 && lead.lead_stage === 'New') {
      newStage = 'Qualified';
      await addActivity(lead.id, 'lead_qualified', `Lead score reached ${scoringResult.score}. Stage automatically transitioned to Qualified.`);
    }

    // 8. Calculate Response Time Metrics
    let firstResponseSeconds = lead.first_response_seconds;
    if (firstResponseSeconds === null) {
      const leadCreatedAt = new Date(lead.created_at);
      firstResponseSeconds = Math.max(1, Math.round((now.getTime() - leadCreatedAt.getTime()) / 1000));
    }
    const lastResponseAt = now.toISOString();

    // 9. Save Agent Message
    const agentMsg = await addMessage(lead.id, 'agent', extraction.reply);

    // 10. Update Lead record in DB
    const updatedLead = await updateLead(lead.id, {
      name: extraction.name,
      budget: extraction.budget,
      preferred_area: extraction.preferred_area,
      property_type: extraction.property_type,
      timeline: extraction.timeline,
      score: scoringResult.score,
      status: scoringResult.status,
      lead_stage: newStage,
      needs_human_review: needsHumanReviewUpdate,
      first_response_seconds: firstResponseSeconds,
      last_response_at: lastResponseAt,
    });

    // 11. Return response
    return NextResponse.json({
      lead: updatedLead,
      reply: extraction.reply,
      userMessage: userMsg,
      agentMessage: agentMsg,
      score: scoringResult.score,
      status: scoringResult.status,
      stage: newStage,
      needs_human_review: needsHumanReviewUpdate
    });

  } catch (error: unknown) {
    console.error('Error in chat API route:', error);
    const message = error instanceof Error ? error.message : 'Unknown error';
    return NextResponse.json({ error: message }, { status: 500 });
  }
}

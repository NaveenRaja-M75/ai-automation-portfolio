import { NextRequest, NextResponse } from 'next/server';
import { getLeads, createLead, updateLead, getMessages, addMessage, addActivity } from '@/lib/db';
import { runAIQualification } from '@/lib/gemini';
import { calculateLeadScore } from '@/lib/scoring';
import { LeadSource, Lead } from '@/types';

// Deterministic Human Review Triggers (consistent with chat route)
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
    const { name, phone, message, leadSource = 'Website' } = body;

    if (!phone || !message) {
      return NextResponse.json({ error: 'Phone and message are required' }, { status: 400 });
    }

    const now = new Date();
    let lead: Lead | null = null;

    // 1. Get or Create Lead based on phone
    const allLeads = await getLeads();
    const existingLead = allLeads.find((l: Lead) => l.phone === phone);

    if (existingLead) {
      lead = existingLead;
      
      // If the existing lead does not have a name, but name was supplied now, update it
      if (!lead.name && name) {
        lead = await updateLead(lead.id, { name });
        await addActivity(lead.id, 'lead_updated', `Name set to "${name}" from contact form intake.`);
      } else {
        await addActivity(lead.id, 'lead_updated', `Lead submitted another inquiry via ${leadSource}.`);
      }
    } else {
      // Create new lead
      lead = await createLead({
        name: name || null,
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

      await addActivity(lead.id, 'lead_created', `Lead captured automatically via ${leadSource} form.`);
    }

    // 2. Save User Message
    const userMsg = await addMessage(lead.id, 'lead', message);

    // 3. Scan for Deterministic Human Review Triggers in initial message
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
        `Flagged for human review. Triggered by keyword in contact form: "${message}". Category: ${triggerMatched.type}.`
      );
    }

    // 4. Retrieve Full Message History for AI Context
    const messages = await getMessages(lead.id);
    const history = messages.map(m => ({
      sender: m.sender,
      content: m.content
    }));

    // 5. Run AI Qualification Engine
    const currentData = {
      name: lead.name,
      budget: lead.budget,
      preferred_area: lead.preferred_area,
      property_type: lead.property_type,
      timeline: lead.timeline,
    };
    
    const extraction = await runAIQualification(history, currentData);

    // 6. Calculate Score & Status
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
    if (scoringResult.score >= 75 && lead.lead_stage === 'New') {
      newStage = 'Qualified';
      await addActivity(lead.id, 'lead_qualified', `Lead score reached ${scoringResult.score}. Stage automatically transitioned to Qualified.`);
    }

    // Calculate Response Time Metrics
    let firstResponseSeconds = lead.first_response_seconds;
    if (firstResponseSeconds === null) {
      const leadCreatedAt = new Date(lead.created_at);
      firstResponseSeconds = Math.max(1, Math.round((now.getTime() - leadCreatedAt.getTime()) / 1000));
    }
    const lastResponseAt = now.toISOString();

    // 8. Save Agent Message (AI reply/outbound welcomes)
    const agentMsg = await addMessage(lead.id, 'agent', extraction.reply);

    // 9. Update Lead record in DB
    const updatedLead = await updateLead(lead.id, {
      name: extraction.name || lead.name,
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

    // 10. Return success response
    return NextResponse.json({
      success: true,
      lead: updatedLead,
      reply: extraction.reply,
      userMessage: userMsg,
      agentMessage: agentMsg,
      score: scoringResult.score,
      status: scoringResult.status,
      stage: newStage
    });

  } catch (error: unknown) {
    console.error('Error in intake API route:', error);
    const message = error instanceof Error ? error.message : 'Unknown error';
    return NextResponse.json({ error: message }, { status: 500 });
  }
}

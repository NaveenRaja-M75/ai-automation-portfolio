import { createClient } from '@supabase/supabase-js';
import { Lead, Message, Activity, SenderType, ActivityType } from '@/types';
import { MOCK_LEADS, MOCK_MESSAGES, MOCK_ACTIVITIES } from './mockData';
import crypto from 'crypto';

const supabaseUrl = process.env.NEXT_PUBLIC_SUPABASE_URL || '';
const supabaseAnonKey = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY || '';

const hasSupabaseCreds = supabaseUrl && supabaseAnonKey && supabaseUrl !== 'your_supabase_url';

export const supabase = hasSupabaseCreds ? createClient(supabaseUrl, supabaseAnonKey) : null;

export function isMockMode(): boolean {
  return !hasSupabaseCreds;
}

// Global variable support for server-side Next.js mock data persistence
interface GlobalMockDb {
  leads: Lead[];
  messages: Message[];
  activities: Activity[];
}

const globalForDb = global as unknown as { mockDb?: GlobalMockDb };

if (!globalForDb.mockDb) {
  globalForDb.mockDb = {
    leads: [...MOCK_LEADS],
    messages: [...MOCK_MESSAGES],
    activities: [...MOCK_ACTIVITIES],
  };
}

const mockDb = globalForDb.mockDb;

// Helper to generate UUIDs in mock mode
function generateUuid() {
  return Math.random().toString(36).substring(2, 15) + Math.random().toString(36).substring(2, 15);
}

// Database CRUD wrapper functions
export async function getLeads(): Promise<Lead[]> {
  if (supabase) {
    const { data, error } = await supabase
      .from('leads')
      .select('*')
      .order('created_at', { ascending: false });
    if (error) throw error;
    return data || [];
  } else {
    // Return mock leads sorted by creation date descending
    return [...mockDb.leads].sort((a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime());
  }
}

export async function getLeadById(id: string): Promise<Lead | null> {
  if (supabase) {
    const { data, error } = await supabase
      .from('leads')
      .select('*')
      .eq('id', id)
      .single();
    if (error) {
      if (error.code === 'PGRST116') return null; // No rows found
      throw error;
    }
    return data;
  } else {
    return mockDb.leads.find(l => l.id === id) || null;
  }
}

export async function getMessages(leadId: string): Promise<Message[]> {
  if (supabase) {
    const { data, error } = await supabase
      .from('messages')
      .select('*')
      .eq('lead_id', leadId)
      .order('created_at', { ascending: true });
    if (error) throw error;
    return data || [];
  } else {
    return mockDb.messages
      .filter(m => m.lead_id === leadId)
      .sort((a, b) => new Date(a.created_at).getTime() - new Date(b.created_at).getTime());
  }
}

export async function getActivities(leadId: string): Promise<Activity[]> {
  if (supabase) {
    const { data, error } = await supabase
      .from('activities')
      .select('*')
      .eq('lead_id', leadId)
      .order('created_at', { ascending: false });
    if (error) throw error;
    return data || [];
  } else {
    return mockDb.activities
      .filter(a => a.lead_id === leadId)
      .sort((a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime());
  }
}

export async function createLead(leadData: Partial<Lead> & { phone: string }): Promise<Lead> {
  const now = new Date().toISOString();
  
  if (supabase) {
    const { data, error } = await supabase
      .from('leads')
      .insert([
        {
          name: leadData.name || null,
          phone: leadData.phone,
          budget: leadData.budget || null,
          preferred_area: leadData.preferred_area || null,
          property_type: leadData.property_type || null,
          timeline: leadData.timeline || null,
          score: leadData.score || 0,
          status: leadData.status || 'Cold',
          lead_source: leadData.lead_source || 'WhatsApp',
          lead_stage: leadData.lead_stage || 'New',
          needs_human_review: leadData.needs_human_review || false,
          first_response_seconds: leadData.first_response_seconds || null,
          last_response_at: leadData.last_response_at || null,
        }
      ])
      .select()
      .single();
    if (error) throw error;
    return data;
  } else {
    // Check if phone already exists
    const existing = mockDb.leads.find(l => l.phone === leadData.phone);
    if (existing) {
      throw new Error(`Lead with phone ${leadData.phone} already exists`);
    }

    const newLead: Lead = {
      id: generateUuid(),
      name: leadData.name || null,
      phone: leadData.phone,
      budget: leadData.budget || null,
      preferred_area: leadData.preferred_area || null,
      property_type: leadData.property_type || null,
      timeline: leadData.timeline || null,
      score: leadData.score || 0,
      status: leadData.status || 'Cold',
      lead_source: leadData.lead_source || 'WhatsApp',
      lead_stage: leadData.lead_stage || 'New',
      needs_human_review: leadData.needs_human_review || false,
      first_response_seconds: leadData.first_response_seconds || null,
      last_response_at: leadData.last_response_at || null,
      created_at: now,
      updated_at: now,
    };
    mockDb.leads.push(newLead);
    return newLead;
  }
}

export async function updateLead(id: string, updates: Partial<Lead>): Promise<Lead> {
  const now = new Date().toISOString();
  
  if (supabase) {
    const { data, error } = await supabase
      .from('leads')
      .update({
        ...updates,
        updated_at: now
      })
      .eq('id', id)
      .select()
      .single();
    if (error) throw error;
    return data;
  } else {
    const leadIndex = mockDb.leads.findIndex(l => l.id === id);
    if (leadIndex === -1) throw new Error(`Lead with ID ${id} not found`);
    
    const updatedLead: Lead = {
      ...mockDb.leads[leadIndex],
      ...updates,
      updated_at: now
    };
    mockDb.leads[leadIndex] = updatedLead;
    return updatedLead;
  }
}

export async function addMessage(leadId: string, sender: SenderType, content: string): Promise<Message> {
  const now = new Date().toISOString();
  
  if (supabase) {
    const { data, error } = await supabase
      .from('messages')
      .insert([
        {
          lead_id: leadId,
          sender,
          content
        }
      ])
      .select()
      .single();
    if (error) throw error;
    return data;
  } else {
    const newMessage: Message = {
      id: generateUuid(),
      lead_id: leadId,
      sender,
      content,
      created_at: now,
    };
    mockDb.messages.push(newMessage);
    return newMessage;
  }
}

export async function addActivity(leadId: string, activity_type: ActivityType, details: string): Promise<Activity> {
  const now = new Date().toISOString();
  
  if (supabase) {
    const { data, error } = await supabase
      .from('activities')
      .insert([
        {
          lead_id: leadId,
          activity_type,
          details
        }
      ])
      .select()
      .single();
    if (error) throw error;
    return data;
  } else {
    const newActivity: Activity = {
      id: generateUuid(),
      lead_id: leadId,
      activity_type,
      details,
      created_at: now,
    };
    mockDb.activities.push(newActivity);
    return newActivity;
  }
}

export async function resetDatabase(): Promise<void> {
  if (supabase) {
    // 1. Clear existing records to avoid foreign key conflicts
    await supabase.from('activities').delete().neq('id', '00000000-0000-0000-0000-000000000000');
    await supabase.from('messages').delete().neq('id', '00000000-0000-0000-0000-000000000000');
    await supabase.from('leads').delete().neq('id', '00000000-0000-0000-0000-000000000000');

    // 2. Map old mock string IDs to valid UUIDs
    const uuidMap: Record<string, string> = {};
    MOCK_LEADS.forEach(l => {
      uuidMap[l.id] = crypto.randomUUID();
    });

    const leadsToInsert = MOCK_LEADS.map(l => ({
      id: uuidMap[l.id],
      name: l.name,
      phone: l.phone,
      budget: l.budget,
      preferred_area: l.preferred_area,
      property_type: l.property_type,
      timeline: l.timeline,
      score: l.score,
      status: l.status,
      lead_source: l.lead_source,
      lead_stage: l.lead_stage,
      needs_human_review: l.needs_human_review,
      first_response_seconds: l.first_response_seconds,
      last_response_at: l.last_response_at,
      created_at: l.created_at,
      updated_at: l.updated_at
    }));

    const { error: le } = await supabase.from('leads').insert(leadsToInsert);
    if (le) throw le;

    const messagesToInsert = MOCK_MESSAGES.map(m => ({
      lead_id: uuidMap[m.lead_id],
      sender: m.sender,
      content: m.content,
      created_at: m.created_at
    }));

    const { error: me } = await supabase.from('messages').insert(messagesToInsert);
    if (me) throw me;

    const activitiesToInsert = MOCK_ACTIVITIES.map(a => ({
      lead_id: uuidMap[a.lead_id],
      activity_type: a.activity_type,
      details: a.details,
      created_at: a.created_at
    }));

    const { error: ae } = await supabase.from('activities').insert(activitiesToInsert);
    if (ae) throw ae;
  } else {
    // Sandbox / Mock Mode reset
    mockDb.leads = [...MOCK_LEADS];
    mockDb.messages = [...MOCK_MESSAGES];
    mockDb.activities = [...MOCK_ACTIVITIES];
  }
}

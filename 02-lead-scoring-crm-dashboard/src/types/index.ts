export type LeadStatus = 'Hot' | 'Warm' | 'Cold';

export type LeadSource =
  | 'Facebook'
  | 'Instagram'
  | 'Google Ads'
  | 'MagicBricks'
  | '99acres'
  | 'Housing'
  | 'Website'
  | 'WhatsApp'
  | 'Other';

export type LeadStage =
  | 'New'
  | 'Qualified'
  | 'Site Visit Scheduled'
  | 'Site Visit Completed'
  | 'Won'
  | 'Lost';

export type SenderType = 'lead' | 'agent' | 'salesperson';

export type ActivityType =
  | 'lead_created'
  | 'lead_updated'
  | 'lead_qualified'
  | 'human_review_requested';

export interface Lead {
  id: string;
  name: string | null;
  phone: string | null;
  budget: string | null;
  preferred_area: string | null;
  property_type: string | null;
  timeline: string | null;
  score: number;
  status: LeadStatus;
  lead_source: LeadSource;
  lead_stage: LeadStage;
  needs_human_review: boolean;
  first_response_seconds: number | null;
  last_response_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface Message {
  id: string;
  lead_id: string;
  sender: SenderType;
  content: string;
  created_at: string;
}

export interface Activity {
  id: string;
  lead_id: string;
  activity_type: ActivityType;
  details: string;
  created_at: string;
}

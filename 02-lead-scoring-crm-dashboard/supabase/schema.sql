-- Drop existing tables if they exist (for clean setup)
DROP TABLE IF EXISTS activities CASCADE;
DROP TABLE IF EXISTS messages CASCADE;
DROP TABLE IF EXISTS leads CASCADE;

-- Enable UUID extension if not enabled
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- Leads Table
CREATE TABLE leads (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  name VARCHAR(255) NULL,
  phone VARCHAR(50) UNIQUE NULL,
  budget VARCHAR(100) NULL,
  preferred_area VARCHAR(255) NULL,
  property_type VARCHAR(100) NULL,
  timeline VARCHAR(100) NULL,
  score INTEGER DEFAULT 0,
  status VARCHAR(20) DEFAULT 'Cold', -- 'Hot', 'Warm', 'Cold'
  lead_source VARCHAR(50) DEFAULT 'WhatsApp', -- 'Facebook', 'Instagram', 'Google Ads', 'MagicBricks', '99acres', 'Housing', 'Website', 'WhatsApp', 'Other'
  lead_stage VARCHAR(50) DEFAULT 'New', -- 'New', 'Qualified', 'Site Visit Scheduled', 'Site Visit Completed', 'Won', 'Lost'
  needs_human_review BOOLEAN DEFAULT FALSE,
  first_response_seconds INTEGER NULL,
  last_response_at TIMESTAMPTZ NULL,
  created_at TIMESTAMPTZ DEFAULT now(),
  updated_at TIMESTAMPTZ DEFAULT now()
);

-- Messages Table
CREATE TABLE messages (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  lead_id UUID REFERENCES leads(id) ON DELETE CASCADE,
  sender VARCHAR(20) NOT NULL CHECK (sender IN ('lead', 'agent', 'salesperson')),
  content TEXT NOT NULL,
  created_at TIMESTAMPTZ DEFAULT now()
);

-- Activities Table
CREATE TABLE activities (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  lead_id UUID REFERENCES leads(id) ON DELETE CASCADE,
  activity_type VARCHAR(50) NOT NULL CHECK (activity_type IN ('lead_created', 'lead_updated', 'lead_qualified', 'human_review_requested')),
  details TEXT NOT NULL,
  created_at TIMESTAMPTZ DEFAULT now()
);

-- Create index on foreign keys for performance
CREATE INDEX IF NOT EXISTS idx_messages_lead_id ON messages(lead_id);
CREATE INDEX IF NOT EXISTS idx_activities_lead_id ON activities(lead_id);
CREATE INDEX IF NOT EXISTS idx_leads_status ON leads(status);
CREATE INDEX IF NOT EXISTS idx_leads_stage ON leads(lead_stage);
CREATE INDEX IF NOT EXISTS idx_leads_needs_human_review ON leads(needs_human_review);

-- Trigger to update updated_at on leads
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
   NEW.updated_at = now();
   RETURN NEW;
END;
$$ language 'plpgsql';

CREATE TRIGGER update_leads_updated_at
    BEFORE UPDATE ON leads
    FOR EACH ROW
    EXECUTE PROCEDURE update_updated_at_column();

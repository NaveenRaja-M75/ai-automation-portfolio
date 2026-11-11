import { LeadStatus } from '@/types';

// Helper to convert budget string to numeric Lakhs for comparison
export function parseBudgetValueInLakhs(budgetStr: string | null): number {
  if (!budgetStr) return 0;
  
  const str = budgetStr.toLowerCase().replace(/,/g, '').trim();
  
  // Case 1: Crores (e.g. "1.5 Cr", "2 Crores")
  if (str.includes('cr') || str.includes('crore')) {
    const match = str.match(/[\d.]+/);
    if (match) {
      const val = parseFloat(match[0]);
      return val * 100; // 1 Crore = 100 Lakhs
    }
  }
  
  // Case 2: Lakhs (e.g. "85 Lakhs", "50lakh")
  if (str.includes('lakh') || str.includes('lk') || str.includes('lac') || (str.endsWith('l') && !isNaN(parseFloat(str)))) {
    const match = str.match(/[\d.]+/);
    if (match) {
      return parseFloat(match[0]);
    }
  }
  
  // Case 3: Raw Numbers (e.g. "8000000", "500000")
  const numMatch = str.match(/\d+/);
  if (numMatch) {
    const val = parseInt(numMatch[0]);
    if (val >= 100000) {
      return val / 100000; // Convert to Lakhs
    }
    return val; // Assume it's already in Lakhs if small number
  }
  
  return 0;
}

// Deterministic Scoring Engine
export function calculateLeadScore(
  lead: {
    budget: string | null;
    timeline: string | null;
    preferred_area: string | null;
    property_type: string | null;
  },
  userMessagesCount: number
): { score: number; status: LeadStatus } {
  let score = 0;

  // 1. Budget Scoring (Max 30 pts)
  const budgetInLakhs = parseBudgetValueInLakhs(lead.budget);
  if (budgetInLakhs >= 80) {
    score += 30;
  } else if (budgetInLakhs >= 40) {
    score += 20;
  } else if (budgetInLakhs > 0) {
    score += 10;
  }

  // 2. Timeline Scoring (Max 30 pts)
  if (lead.timeline) {
    const timelineStr = lead.timeline.toLowerCase().trim();
    if (
      timelineStr.includes('immediate') ||
      timelineStr.includes('now') ||
      timelineStr.includes('ready') ||
      timelineStr.includes('1 month') ||
      timelineStr.includes('30 days') ||
      timelineStr.includes('15 days') ||
      timelineStr.includes('instant') ||
      timelineStr.includes('asap')
    ) {
      score += 30;
    } else if (
      timelineStr.includes('soon') ||
      timelineStr.includes('2 month') ||
      timelineStr.includes('3 month') ||
      timelineStr.includes('90 days') ||
      timelineStr.includes('1-3') ||
      timelineStr.includes('2-3')
    ) {
      score += 20;
    } else if (
      timelineStr.includes('6 month') ||
      timelineStr.includes('4 month') ||
      timelineStr.includes('5 month') ||
      timelineStr.includes('year') ||
      timelineStr.includes('later') ||
      timelineStr.includes('browsing') ||
      timelineStr.includes('exploring')
    ) {
      score += 10;
    }
  }

  // 3. Requirement Clarity (Max 20 pts)
  const hasArea = !!lead.preferred_area && lead.preferred_area.trim().length > 0;
  const hasPropType = !!lead.property_type && lead.property_type.trim().length > 0;
  
  if (hasArea && hasPropType) {
    score += 20;
  } else if (hasArea || hasPropType) {
    score += 10;
  }

  // 4. Engagement Score (Max 20 pts)
  if (userMessagesCount >= 4) {
    score += 20;
  } else if (userMessagesCount >= 2) {
    score += 10;
  } else if (userMessagesCount === 1) {
    score += 5;
  }

  // Calculate status based on total score
  let status: LeadStatus = 'Cold';
  if (score >= 75) {
    status = 'Hot';
  } else if (score >= 40) {
    status = 'Warm';
  }

  return { score, status };
}

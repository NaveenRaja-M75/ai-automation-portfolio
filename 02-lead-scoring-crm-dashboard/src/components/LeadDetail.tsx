import React from 'react';
import { Lead, Activity, LeadStage } from '@/types';
import { parseBudgetValueInLakhs } from '@/lib/scoring';

interface LeadDetailProps {
  lead: Lead;
  activities: Activity[];
  onUpdateStage: (leadId: string, stage: LeadStage) => void;
  onToggleHumanReview: (leadId: string, needsReview: boolean) => void;
  messagesCount: number;
}

export default function LeadDetail({
  lead,
  activities,
  onUpdateStage,
  onToggleHumanReview,
  messagesCount,
}: LeadDetailProps) {
  // Compute scoring details for transparent visual explanation
  // 1. Budget Score
  let budgetScore = 0;
  const budgetStr = lead.budget || '';
  const parsedBudget = parseBudgetValueInLakhs(budgetStr);
  if (parsedBudget >= 80) budgetScore = 30;
  else if (parsedBudget >= 40) budgetScore = 20;
  else if (parsedBudget > 0) budgetScore = 10;

  // 2. Timeline Score
  let timelineScore = 0;
  if (lead.timeline) {
    const timelineStr = lead.timeline.toLowerCase();
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
      timelineScore = 30;
    } else if (
      timelineStr.includes('soon') ||
      timelineStr.includes('2 month') ||
      timelineStr.includes('3 month') ||
      timelineStr.includes('90 days') ||
      timelineStr.includes('1-3') ||
      timelineStr.includes('2-3')
    ) {
      timelineScore = 20;
    } else if (
      timelineStr.includes('6 month') ||
      timelineStr.includes('4 month') ||
      timelineStr.includes('5 month') ||
      timelineStr.includes('year') ||
      timelineStr.includes('later') ||
      timelineStr.includes('browsing') ||
      timelineStr.includes('exploring')
    ) {
      timelineScore = 10;
    }
  }

  // 3. Requirement Clarity Score
  const hasArea = !!lead.preferred_area && lead.preferred_area.trim().length > 0;
  const hasPropType = !!lead.property_type && lead.property_type.trim().length > 0;
  const clarityScore = hasArea && hasPropType ? 20 : (hasArea || hasPropType ? 10 : 0);

  // 4. Engagement Score
  const engagementScore = messagesCount >= 4 ? 20 : (messagesCount >= 2 ? 10 : (messagesCount === 1 ? 5 : 0));

  // Helpers for styling
  const getStatusColor = (status: string) => {
    switch (status) {
      case 'Hot':
        return 'bg-emerald-950/50 text-emerald-400 border border-emerald-900/80';
      case 'Warm':
        return 'bg-amber-950/50 text-amber-400 border border-amber-900/80';
      default:
        return 'bg-slate-900 text-slate-400 border border-slate-800';
    }
  };

  const getActivityBadge = (type: string) => {
    switch (type) {
      case 'lead_created':
        return '🟢 Created';
      case 'lead_updated':
        return '✏️ Updated';
      case 'lead_qualified':
        return '🔥 Qualified';
      case 'human_review_requested':
        return '⚠️ Flagged';
      default:
        return 'ℹ️ Event';
    }
  };

  return (
    <div className="bg-slate-900 border border-slate-800 rounded-xl flex flex-col h-[calc(100vh-140px)] shadow-xl overflow-hidden">
      {/* Scrollable details container */}
      <div className="flex-1 overflow-y-auto p-6 space-y-6">
        {/* Profile header */}
        <div className="flex justify-between items-start border-b border-slate-800 pb-4">
          <div className="space-y-1">
            <h2 className="text-xl font-black text-white">{lead.name || 'Anonymous Lead'}</h2>
            <p className="text-xs text-slate-400 font-mono flex items-center gap-2">
              <span>📞 {lead.phone}</span>
              <span>&bull;</span>
              <span>Source: {lead.lead_source}</span>
            </p>
          </div>
          <div className="flex items-center gap-2">
            <span className={`px-3 py-1 text-xs font-bold rounded-lg ${getStatusColor(lead.status)}`}>
              {lead.status}
            </span>
          </div>
        </div>

        {/* Action controls (Stage & Handoff override) */}
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4 bg-slate-950/40 border border-slate-850 p-4 rounded-xl">
          <div>
            <label className="block text-[10px] font-bold text-slate-400 uppercase tracking-wider mb-1">
              Sales Stage
            </label>
            <select
              value={lead.lead_stage}
              onChange={e => onUpdateStage(lead.id, e.target.value as LeadStage)}
              className="bg-slate-900 border border-slate-800 rounded-lg px-3 py-1.5 text-xs text-slate-200 focus:outline-none focus:border-slate-700 w-full"
            >
              <option value="New">New</option>
              <option value="Qualified">Qualified</option>
              <option value="Site Visit Scheduled">Site Visit Scheduled</option>
              <option value="Site Visit Completed">Site Visit Completed</option>
              <option value="Won">Won</option>
              <option value="Lost">Lost</option>
            </select>
          </div>

          <div>
            <label className="block text-[10px] font-bold text-slate-400 uppercase tracking-wider mb-1">
              Human Attention Status
            </label>
            {lead.needs_human_review ? (
              <button
                onClick={() => onToggleHumanReview(lead.id, false)}
                className="w-full bg-red-950/40 border border-red-900/60 text-red-400 hover:bg-emerald-950/40 hover:border-emerald-900/60 hover:text-emerald-400 font-semibold py-1.5 px-3 rounded-lg text-xs transition-colors flex items-center justify-center gap-1.5"
              >
                <span>⚠️ In Queue</span> &bull; <span className="underline text-[10px]">Resolve Review</span>
              </button>
            ) : (
              <button
                onClick={() => onToggleHumanReview(lead.id, true)}
                className="w-full bg-slate-900 border border-slate-850 hover:border-red-900/60 hover:text-red-400 py-1.5 px-3 rounded-lg text-xs text-slate-400 transition-colors flex items-center justify-center gap-1"
              >
                Flag Human Review
              </button>
            )}
          </div>
        </div>

        {/* Requirements Card */}
        <div className="bg-slate-950/30 border border-slate-850 rounded-xl p-5 space-y-4">
          <h3 className="font-bold text-xs text-slate-300 uppercase tracking-wider">Collected Requirements</h3>
          
          <div className="grid grid-cols-2 gap-4">
            <div className="space-y-1">
              <span className="text-[10px] text-slate-500 font-semibold">Name</span>
              <p className="text-xs text-slate-200 font-medium">{lead.name || 'Not collected'}</p>
            </div>
            
            <div className="space-y-1">
              <span className="text-[10px] text-slate-500 font-semibold">Budget Range</span>
              <p className="text-xs text-slate-200 font-medium">{lead.budget || 'Not collected'}</p>
            </div>

            <div className="space-y-1">
              <span className="text-[10px] text-slate-500 font-semibold">Preferred Area</span>
              <p className="text-xs text-slate-200 font-medium">{lead.preferred_area || 'Not collected'}</p>
            </div>

            <div className="space-y-1">
              <span className="text-[10px] text-slate-500 font-semibold">Property Type</span>
              <p className="text-xs text-slate-200 font-medium">{lead.property_type || 'Not collected'}</p>
            </div>

            <div className="space-y-1 col-span-2">
              <span className="text-[10px] text-slate-500 font-semibold">Timeline to Purchase</span>
              <p className="text-xs text-slate-200 font-medium">{lead.timeline || 'Not collected'}</p>
            </div>
          </div>
        </div>

        {/* Deterministic Scoring breakdown */}
        <div className="bg-slate-950/30 border border-slate-850 rounded-xl p-5 space-y-4">
          <div className="flex justify-between items-center">
            <h3 className="font-bold text-xs text-slate-300 uppercase tracking-wider">Scoring Matrix Analysis</h3>
            <span className="text-xs font-mono font-bold text-emerald-400">{lead.score} / 100 pts</span>
          </div>

          <div className="space-y-3">
            {/* Budget Points */}
            <div className="flex items-center justify-between text-xs">
              <div className="space-y-0.5">
                <span className="text-slate-300 font-medium">Budget Score</span>
                <p className="text-[9px] text-slate-500">Max 30 pts (&ge;80L = 30, &ge;40L = 20, &gt;0 = 10)</p>
              </div>
              <span className="font-mono text-slate-400 font-semibold">{budgetScore} / 30</span>
            </div>

            {/* Timeline Points */}
            <div className="flex items-center justify-between text-xs">
              <div className="space-y-0.5">
                <span className="text-slate-300 font-medium">Timeline Urgency</span>
                <p className="text-[9px] text-slate-500">Max 30 pts (Immediate = 30, 1-3m = 20, 3-6m = 10)</p>
              </div>
              <span className="font-mono text-slate-400 font-semibold">{timelineScore} / 30</span>
            </div>

            {/* Requirement Clarity Points */}
            <div className="flex items-center justify-between text-xs">
              <div className="space-y-0.5">
                <span className="text-slate-300 font-medium">Requirement Clarity</span>
                <p className="text-[9px] text-slate-500">Max 20 pts (Area + Type = 20, One present = 10)</p>
              </div>
              <span className="font-mono text-slate-400 font-semibold">{clarityScore} / 20</span>
            </div>

            {/* Engagement Level Points */}
            <div className="flex items-center justify-between text-xs">
              <div className="space-y-0.5">
                <span className="text-slate-300 font-medium">Customer Engagement</span>
                <p className="text-[9px] text-slate-500">Max 20 pts (4+ msgs = 20, 2-3 msgs = 10, 1 msg = 5)</p>
              </div>
              <span className="font-mono text-slate-400 font-semibold">{engagementScore} / 20</span>
            </div>
          </div>
        </div>

        {/* Response times and Created metadata */}
        <div className="grid grid-cols-2 gap-4 text-xs border-t border-slate-800 pt-5 text-slate-400">
          <div>
            <span className="text-[10px] text-slate-500 font-semibold">Avg AI response latency</span>
            <p className="text-slate-300 mt-1 font-mono">
              {lead.first_response_seconds !== null ? `${lead.first_response_seconds} seconds` : 'No agent response yet'}
            </p>
          </div>
          <div>
            <span className="text-[10px] text-slate-500 font-semibold">Last AI Contact</span>
            <p className="text-slate-300 mt-1 font-mono">
              {lead.last_response_at ? new Date(lead.last_response_at).toLocaleTimeString() : 'N/A'}
            </p>
          </div>
        </div>

        {/* Audit Log Activities */}
        <div className="border-t border-slate-800 pt-5 space-y-4">
          <h3 className="font-bold text-xs text-slate-300 uppercase tracking-wider">System Activity Audit Logs</h3>
          
          <div className="space-y-3 max-h-48 overflow-y-auto">
            {activities.length === 0 ? (
              <p className="text-xs text-slate-500 italic">No audit records logged.</p>
            ) : (
              activities.map(activity => (
                <div key={activity.id} className="bg-slate-950/40 border border-slate-850/50 p-3 rounded-lg flex flex-col gap-1">
                  <div className="flex justify-between items-center text-[10px]">
                    <span className="font-bold text-slate-300">
                      {getActivityBadge(activity.activity_type)}
                    </span>
                    <span className="text-slate-500 font-mono">
                      {new Date(activity.created_at).toLocaleTimeString()}
                    </span>
                  </div>
                  <p className="text-[10px] text-slate-400 leading-relaxed">
                    {activity.details}
                  </p>
                </div>
              ))
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

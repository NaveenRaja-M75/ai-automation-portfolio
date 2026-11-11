'use client';

import React from 'react';
import { Lead } from '@/types';

interface DashboardStatsProps {
  leads: Lead[];
}

export default function DashboardStats({ leads }: DashboardStatsProps) {
  // 1. Calculate general metrics
  const totalLeads = leads.length;
  
  const needsReview = leads.filter(l => l.needs_human_review).length;
  
  // Calculate average lead score
  const avgLeadScore = totalLeads > 0
    ? Math.round(leads.reduce((acc, curr) => acc + curr.score, 0) / totalLeads)
    : 0;

  // Calculate qualification rate (Hot + Warm leads)
  const qualifiedLeads = leads.filter(l => l.status === 'Hot' || l.status === 'Warm').length;
  const qualificationRate = totalLeads > 0
    ? Math.round((qualifiedLeads / totalLeads) * 100)
    : 0;

  // Calculate average response time
  const respondedLeads = leads.filter(l => l.first_response_seconds !== null);
  const avgResponseTime = respondedLeads.length > 0
    ? Math.round(respondedLeads.reduce((acc, curr) => acc + (curr.first_response_seconds || 0), 0) / respondedLeads.length)
    : 0;

  // 2. Lead Source Breakdown
  const sources: Record<string, number> = {
    Facebook: 0,
    Instagram: 0,
    'Google Ads': 0,
    MagicBricks: 0,
    '99acres': 0,
    Housing: 0,
    Website: 0,
    WhatsApp: 0,
    Other: 0,
  };
  leads.forEach(l => {
    if (l.lead_source in sources) {
      sources[l.lead_source]++;
    } else {
      sources.Other++;
    }
  });

  // Sort sources for visual priority
  const sortedSources = Object.entries(sources).sort((a, b) => b[1] - a[1]);
  const maxSourceCount = Math.max(...Object.values(sources), 1);

  // 3. Lead Stage Funnel
  const stages = {
    New: 0,
    Qualified: 0,
    'Site Visit Scheduled': 0,
    'Site Visit Completed': 0,
    Won: 0,
    Lost: 0,
  };
  leads.forEach(l => {
    if (l.lead_stage in stages) {
      stages[l.lead_stage as keyof typeof stages]++;
    }
  });

  const maxStageCount = Math.max(...Object.values(stages), 1);

  return (
    <div className="space-y-6">
      {/* 5 KPI Cards Grid */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-5 gap-4">
        {/* Total Leads */}
        <div className="bg-slate-900 border border-slate-800 rounded-xl p-4 hover:border-slate-700 transition-all duration-200 shadow-lg">
          <div className="flex justify-between items-start">
            <span className="text-[10px] font-semibold text-slate-400 uppercase tracking-wider">Total Leads</span>
            <span className="text-base">👥</span>
          </div>
          <div className="mt-3 flex items-baseline gap-1.5">
            <span className="text-2xl font-extrabold text-white">{totalLeads}</span>
            <span className="text-[9px] text-slate-500 font-mono">leads</span>
          </div>
          <p className="text-[9px] text-slate-500 mt-2">All integrated channels</p>
        </div>

        {/* Average Lead Score */}
        <div className="bg-slate-900 border border-slate-800 rounded-xl p-4 hover:border-slate-700 transition-all duration-200 shadow-lg glow-emerald">
          <div className="flex justify-between items-start">
            <span className="text-[10px] font-semibold text-slate-400 uppercase tracking-wider">Avg Lead Score</span>
            <span className="text-base">🎯</span>
          </div>
          <div className="mt-3 flex items-baseline gap-1.5">
            <span className="text-2xl font-extrabold text-white">{avgLeadScore}</span>
            <span className="text-[9px] text-emerald-400 font-bold bg-emerald-950/40 px-1.5 py-0.5 rounded border border-emerald-900/40">
              / 100
            </span>
          </div>
          <p className="text-[9px] text-slate-500 mt-2">Score weighted dynamically</p>
        </div>

        {/* Qualification Rate */}
        <div className="bg-slate-900 border border-slate-800 rounded-xl p-4 hover:border-slate-700 transition-all duration-200 shadow-lg">
          <div className="flex justify-between items-start">
            <span className="text-[10px] font-semibold text-slate-400 uppercase tracking-wider">Qualification Rate</span>
            <span className="text-base">📈</span>
          </div>
          <div className="mt-3 flex items-baseline gap-1.5">
            <span className="text-2xl font-extrabold text-white">{qualificationRate}%</span>
            <span className="text-[9px] text-emerald-400 font-semibold bg-emerald-950/30 px-1.5 py-0.5 rounded">
              {qualifiedLeads} qualified
            </span>
          </div>
          <p className="text-[9px] text-slate-500 mt-2">Hot + Warm lead percentage</p>
        </div>

        {/* Average Response Time */}
        <div className="bg-slate-900 border border-slate-800 rounded-xl p-4 hover:border-slate-700 transition-all duration-200 shadow-lg glow-amber">
          <div className="flex justify-between items-start">
            <span className="text-[10px] font-semibold text-slate-400 uppercase tracking-wider">Avg Response Time</span>
            <span className="text-base">⚡</span>
          </div>
          <div className="mt-3 flex items-baseline gap-1.5">
            <span className="text-2xl font-extrabold text-white">
              {avgResponseTime > 0 ? `${avgResponseTime}s` : '--'}
            </span>
            <span className="text-[9px] text-amber-400 font-semibold bg-amber-950/40 px-1.5 py-0.5 rounded">
              Instant AI
            </span>
          </div>
          <p className="text-[9px] text-slate-500 mt-2">WhatsApp response latency</p>
        </div>

        {/* Human Review Queue */}
        <div className={`bg-slate-900 border rounded-xl p-4 hover:border-slate-700 transition-all duration-200 shadow-lg ${
          needsReview > 0 ? 'border-red-900/60 glow-red' : 'border-slate-800'
        }`}>
          <div className="flex justify-between items-start">
            <span className="text-[10px] font-semibold text-slate-400 uppercase tracking-wider">Review Queue</span>
            <span className="text-base">⚠️</span>
          </div>
          <div className="mt-3 flex items-baseline gap-1.5">
            <span className={`text-2xl font-extrabold ${needsReview > 0 ? 'text-rose-400 animate-pulse' : 'text-white'}`}>
              {needsReview}
            </span>
            {needsReview > 0 ? (
              <span className="text-[9px] text-rose-400 font-bold bg-rose-950/60 px-1.5 py-0.5 rounded border border-rose-900/40">
                Action Req
              </span>
            ) : (
              <span className="text-[9px] text-slate-400 font-medium bg-slate-800 px-1.5 py-0.5 rounded">
                Cleared
              </span>
            )}
          </div>
          <p className="text-[9px] text-slate-500 mt-2">Awaiting agent interception</p>
        </div>
      </div>

      {/* Funnel and Sources Graphs Grid */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* Lead Stage Funnel */}
        <div className="bg-slate-900 border border-slate-800 rounded-xl p-6 shadow-xl">
          <div className="flex items-center justify-between mb-4 border-b border-slate-800 pb-3">
            <h3 className="font-bold text-sm text-slate-200">Conversion Funnel Status</h3>
            <span className="text-[10px] text-slate-500 uppercase tracking-widest font-mono">Stage distribution</span>
          </div>
          <div className="space-y-4">
            {Object.entries(stages).map(([stage, count]) => {
              const percentage = Math.round((count / maxStageCount) * 100);
              // Pick bar color based on stage
              let barColor = 'bg-slate-750';
              if (stage === 'New') barColor = 'bg-indigo-650';
              if (stage === 'Qualified') barColor = 'bg-emerald-600';
              if (stage === 'Site Visit Scheduled' || stage === 'Site Visit Completed') barColor = 'bg-amber-600';
              if (stage === 'Won') barColor = 'bg-emerald-500';
              if (stage === 'Lost') barColor = 'bg-rose-950/60';

              return (
                <div key={stage} className="space-y-1">
                  <div className="flex justify-between text-xs">
                    <span className="text-slate-300 font-medium">{stage}</span>
                    <span className="text-slate-400 font-mono">{count} leads</span>
                  </div>
                  <div className="w-full bg-slate-950 rounded-full h-3 overflow-hidden border border-slate-850">
                    <div
                      className={`h-full rounded-full transition-all duration-500 ${barColor}`}
                      style={{ width: `${totalLeads > 0 ? percentage : 0}%` }}
                    ></div>
                  </div>
                </div>
              );
            })}
          </div>
        </div>

        {/* Lead Source Breakdown */}
        <div className="bg-slate-900 border border-slate-800 rounded-xl p-6 shadow-xl">
          <div className="flex items-center justify-between mb-4 border-b border-slate-800 pb-3">
            <h3 className="font-bold text-sm text-slate-200">Lead Generation Channels</h3>
            <span className="text-[10px] text-slate-500 uppercase tracking-widest font-mono">Source breakdown</span>
          </div>
          <div className="space-y-3.5">
            {sortedSources.map(([source, count]) => {
              const percentage = Math.round((count / maxSourceCount) * 100);
              // Pick bar color based on channel
              let barColor = 'bg-slate-700';
              if (source === 'WhatsApp') barColor = 'bg-emerald-500';
              if (source === 'Facebook') barColor = 'bg-blue-600';
              if (source === 'Instagram') barColor = 'bg-pink-600';
              if (source === 'Google Ads') barColor = 'bg-amber-500';
              if (source === 'Website') barColor = 'bg-cyan-500';

              return (
                <div key={source} className="flex items-center gap-3 text-xs">
                  <span className="w-24 text-slate-400 font-medium truncate">{source}</span>
                  <div className="flex-1 bg-slate-950 rounded-full h-2.5 overflow-hidden border border-slate-850">
                    <div
                      className={`h-full rounded-full transition-all duration-500 ${barColor}`}
                      style={{ width: `${totalLeads > 0 ? percentage : 0}%` }}
                    ></div>
                  </div>
                  <span className="w-14 text-right text-slate-300 font-mono">{count} ({totalLeads > 0 ? Math.round((count / totalLeads) * 100) : 0}%)</span>
                </div>
              );
            })}
          </div>
        </div>
      </div>
    </div>
  );
}

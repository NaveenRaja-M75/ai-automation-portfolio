'use client';

import React, { useState } from 'react';
import { Lead } from '@/types';

interface LeadListProps {
  leads: Lead[];
  selectedLeadId: string | null;
  onSelectLead: (leadId: string) => void;
  filterMode: 'all' | 'hot' | 'review';
}

export default function LeadList({
  leads,
  selectedLeadId,
  onSelectLead,
  filterMode: initialFilterMode,
}: LeadListProps) {
  const [searchTerm, setSearchTerm] = useState('');
  const [statusFilter, setStatusFilter] = useState<'All' | 'Hot' | 'Warm' | 'Cold'>('All');
  const [reviewOnly, setReviewOnly] = useState(initialFilterMode === 'review');
  const [sortBy, setSortBy] = useState<'newest' | 'score'>('newest');

  const downloadCsv = () => {
    if (leads.length === 0) return;
    
    // CSV headers
    const headers = [
      'ID',
      'Name',
      'Phone',
      'Budget',
      'Preferred Area',
      'Property Type',
      'Timeline',
      'Score',
      'Status',
      'Lead Source',
      'Lead Stage',
      'Needs Human Review',
      'Created At'
    ];
    
    // Map leads to CSV rows
    const rows = leads.map(l => [
      l.id,
      l.name || 'Anonymous',
      l.phone || '',
      l.budget || '',
      l.preferred_area || '',
      l.property_type || '',
      l.timeline || '',
      l.score,
      l.status,
      l.lead_source,
      l.lead_stage,
      l.needs_human_review ? 'Yes' : 'No',
      l.created_at
    ]);
    
    // Construct CSV content
    const csvContent = [
      headers.join(','),
      ...rows.map(row => row.map(val => `"${String(val).replace(/"/g, '""')}"`).join(','))
    ].join('\n');
    
    // Create download link
    const blob = new Blob([csvContent], { type: 'text/csv;charset=utf-8;' });
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.setAttribute('href', url);
    link.setAttribute('download', `apex_realty_leads_${new Date().toISOString().slice(0, 10)}.csv`);
    link.style.visibility = 'hidden';
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
  };

  // Sync state if initialFilterMode changes
  React.useEffect(() => {
    if (initialFilterMode === 'review') {
      setReviewOnly(true);
      setStatusFilter('All');
    } else if (initialFilterMode === 'hot') {
      setStatusFilter('Hot');
      setReviewOnly(false);
    } else {
      setStatusFilter('All');
      setReviewOnly(false);
    }
  }, [initialFilterMode]);

  // Handle lead filter/sort logic
  const filteredLeads = leads
    .filter(lead => {
      // 1. Search term match (Name / Phone)
      const name = lead.name || '';
      const phone = lead.phone || '';
      const matchesSearch =
        name.toLowerCase().includes(searchTerm.toLowerCase()) ||
        phone.includes(searchTerm);

      // 2. Status match
      const matchesStatus = statusFilter === 'All' || lead.status === statusFilter;

      // 3. Human review match
      const matchesReview = !reviewOnly || lead.needs_human_review;

      return matchesSearch && matchesStatus && matchesReview;
    })
    .sort((a, b) => {
      if (sortBy === 'score') {
        return b.score - a.score;
      } else {
        return new Date(b.created_at).getTime() - new Date(a.created_at).getTime();
      }
    });

  // Helper styles for score badge
  const getScoreColor = (score: number) => {
    if (score >= 75) return 'text-emerald-400 bg-emerald-950/40 border-emerald-900/60';
    if (score >= 40) return 'text-amber-400 bg-amber-950/40 border-amber-900/60';
    return 'text-slate-400 bg-slate-800/40 border-slate-700/60';
  };

  const getSourceBadgeColor = (source: string) => {
    switch (source) {
      case 'WhatsApp':
        return 'bg-emerald-950/60 text-emerald-400 border-emerald-900/50';
      case 'Facebook':
        return 'bg-blue-950/60 text-blue-400 border-blue-900/50';
      case 'Instagram':
        return 'bg-pink-950/60 text-pink-400 border-pink-900/50';
      case 'Google Ads':
        return 'bg-amber-950/60 text-amber-400 border-amber-900/50';
      case 'Website':
        return 'bg-cyan-950/60 text-cyan-400 border-cyan-900/50';
      default:
        return 'bg-slate-900/60 text-slate-400 border-slate-850';
    }
  };

  return (
    <div className="bg-slate-900 border border-slate-800 rounded-xl flex flex-col h-[calc(100vh-140px)] shadow-xl">
      {/* Search & Header Section */}
      <div className="p-4 border-b border-slate-800 space-y-3 shrink-0">
        <div className="flex justify-between items-center">
          <h3 className="font-bold text-sm text-slate-200">Leads Console</h3>
          <div className="flex items-center gap-2">
            <button
              onClick={downloadCsv}
              className="text-[9px] font-bold bg-slate-800 hover:bg-slate-750 border border-slate-700/50 hover:border-slate-650 px-2 py-0.5 rounded text-slate-300 hover:text-white transition-all cursor-pointer flex items-center gap-1"
              title="Export leads to CSV"
            >
              📥 Export CSV
            </button>
            <span className="text-[10px] text-slate-500 font-mono">{filteredLeads.length} items</span>
          </div>
        </div>
        
        {/* Search input */}
        <div className="relative">
          <input
            type="text"
            placeholder="Search name or phone..."
            value={searchTerm}
            onChange={e => setSearchTerm(e.target.value)}
            className="w-full bg-slate-950 border border-slate-850 rounded-lg py-2 pl-3 pr-8 text-xs text-slate-200 focus:outline-none focus:border-slate-700 transition-colors"
          />
          <span className="absolute right-2.5 top-2.5 text-xs text-slate-500">🔍</span>
        </div>

        {/* Filter controls */}
        <div className="flex flex-wrap gap-1.5 pt-1">
          {/* Status Tabs */}
          {(['All', 'Hot', 'Warm', 'Cold'] as const).map(tab => (
            <button
              key={tab}
              onClick={() => {
                setStatusFilter(tab);
                if (tab !== 'All') setReviewOnly(false); // remove conflict
              }}
              className={`px-2.5 py-1 rounded-md text-[10px] font-semibold border transition-all ${
                statusFilter === tab && !reviewOnly
                  ? 'bg-slate-800 border-slate-750 text-white shadow-inner'
                  : 'bg-transparent border-slate-850 text-slate-400 hover:text-slate-200 hover:border-slate-800'
              }`}
            >
              {tab}
            </button>
          ))}

          {/* Human Review queue toggle */}
          <button
            onClick={() => {
              setReviewOnly(!reviewOnly);
              if (!reviewOnly) setStatusFilter('All');
            }}
            className={`px-2.5 py-1 rounded-md text-[10px] font-bold border transition-all flex items-center gap-1 ${
              reviewOnly
                ? 'bg-red-950/40 border-red-900/60 text-rose-400 shadow-inner glow-red'
                : 'bg-transparent border-slate-850 text-slate-400 hover:text-slate-200 hover:border-slate-800'
            }`}
          >
            ⚠️ Review
          </button>
        </div>

        {/* Sort and Secondary metrics */}
        <div className="flex justify-between items-center text-[10px] text-slate-500 pt-1">
          <span>Sort by:</span>
          <div className="flex gap-2">
            <button
              onClick={() => setSortBy('newest')}
              className={`hover:text-slate-300 transition-colors ${sortBy === 'newest' ? 'text-slate-200 font-semibold' : ''}`}
            >
              Newest
            </button>
            <span>|</span>
            <button
              onClick={() => setSortBy('score')}
              className={`hover:text-slate-300 transition-colors ${sortBy === 'score' ? 'text-slate-200 font-semibold' : ''}`}
            >
              Score
            </button>
          </div>
        </div>
      </div>

      {/* Leads Scroll Area */}
      <div className="flex-1 overflow-y-auto divide-y divide-slate-850">
        {filteredLeads.length === 0 ? (
          <div className="h-full flex flex-col items-center justify-center p-6 text-center text-slate-500">
            <span className="text-2xl mb-2">📭</span>
            <p className="text-xs font-semibold text-slate-400">No leads found</p>
            <p className="text-[10px] text-slate-500 mt-1 max-w-[200px]">
              Try adjusting your filter search criteria or create a new lead in the simulator.
            </p>
          </div>
        ) : (
          filteredLeads.map(lead => {
            const isSelected = selectedLeadId === lead.id;
            return (
              <button
                key={lead.id}
                onClick={() => onSelectLead(lead.id)}
                className={`w-full text-left p-4 hover:bg-slate-800/30 transition-all duration-200 flex flex-col gap-2 relative ${
                  isSelected ? 'bg-slate-800/40 border-l-2 border-emerald-500' : ''
                }`}
              >
                {/* Score & Needs Review Flag Row */}
                <div className="flex justify-between items-start w-full">
                  <div className="flex items-center gap-1.5">
                    <span className="text-xs font-bold text-slate-200 truncate max-w-[120px]">
                      {lead.name || 'Anonymous Lead'}
                    </span>
                    {lead.needs_human_review && (
                      <span
                        className="w-1.5 h-1.5 rounded-full bg-red-500 inline-block animate-ping"
                        title="Needs human review"
                      />
                    )}
                  </div>
                  <div className={`text-[10px] font-mono font-bold px-1.5 py-0.5 rounded border ${getScoreColor(lead.score)}`}>
                    Score: {lead.score}
                  </div>
                </div>

                {/* Phone & Source row */}
                <div className="flex justify-between items-center text-[10px] text-slate-400">
                  <span className="font-mono">{lead.phone || 'No Phone'}</span>
                  <span className={`px-1.5 py-0.2 rounded border text-[9px] font-medium ${getSourceBadgeColor(lead.lead_source)}`}>
                    {lead.lead_source}
                  </span>
                </div>

                {/* Requirements short description */}
                {lead.property_type || lead.preferred_area ? (
                  <p className="text-[10px] text-slate-500 truncate mt-0.5">
                    {lead.property_type || 'Property unspecified'} &bull; {lead.preferred_area || 'Area unspecified'}
                  </p>
                ) : (
                  <p className="text-[10px] text-slate-600 italic mt-0.5">
                    Requirements not collected yet
                  </p>
                )}
              </button>
            );
          })
        )}
      </div>
    </div>
  );
}

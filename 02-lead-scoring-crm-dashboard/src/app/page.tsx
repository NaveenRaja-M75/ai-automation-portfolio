'use client';

import React, { useState, useEffect, useCallback } from 'react';
import { Lead, Message, Activity, LeadStage, LeadSource } from '@/types';
import Sidebar from '@/components/Sidebar';
import SetupBanner from '@/components/SetupBanner';
import DashboardStats from '@/components/DashboardStats';
import LeadList from '@/components/LeadList';
import LeadDetail from '@/components/LeadDetail';
import ChatSimulator from '@/components/ChatSimulator';

export default function Page() {
  const [currentTab, setCurrentTab] = useState('dashboard'); // 'dashboard', 'leads', 'review'
  const [leads, setLeads] = useState<Lead[]>([]);
  const [selectedLeadId, setSelectedLeadId] = useState<string | null>(null);
  
  // Details for selected lead
  const [selectedLead, setSelectedLead] = useState<Lead | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [activities, setActivities] = useState<Activity[]>([]);
  
  const [loading, setLoading] = useState(true);
  const [isCreatingLead, setIsCreatingLead] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [showPlaybook, setShowPlaybook] = useState(true); // Default to open so prospects see it immediately
  const [isResetting, setIsResetting] = useState(false);
  const [toast, setToast] = useState<{ message: string; subMessage: string; type: 'success' | 'error' } | null>(null);

  // Auto-dismiss toast
  useEffect(() => {
    if (toast) {
      const timer = setTimeout(() => setToast(null), 5000);
      return () => clearTimeout(timer);
    }
  }, [toast]);

  const handleResetDemo = async () => {
    if (!confirm('Are you sure you want to delete all current leads, messages, and activities and restore the default high-fidelity demo dataset?')) return;
    setIsResetting(true);
    try {
      const response = await fetch('/api/leads/reset-demo', { method: 'POST' });
      if (!response.ok) throw new Error('Reset failed');
      const data = await response.json();
      
      setSelectedLeadId(null);
      setSelectedLead(null);
      await fetchLeads(true);

      setToast({
        message: 'Demo dataset restored successfully',
        subMessage: `Loaded ${data.counts?.leads || 0} leads, ${data.counts?.messages || 0} messages, and ${data.counts?.activities || 0} activity logs.`,
        type: 'success'
      });
    } catch (err) {
      console.error(err);
      setToast({
        message: 'Database reset failed',
        subMessage: 'Could not restore the default demo dataset. Check server logs.',
        type: 'error'
      });
    } finally {
      setIsResetting(false);
    }
  };

  // Form states for manual lead creation
  const [newPhone, setNewPhone] = useState('');
  const [newName, setNewName] = useState('');
  const [newSource, setNewSource] = useState<LeadSource>('WhatsApp');

  // Load all leads from API
  const fetchLeads = useCallback(async (selectFirst = false, targetIdToSelect: string | null = null) => {
    try {
      const response = await fetch('/api/leads');
      if (!response.ok) throw new Error('Failed to fetch leads');
      const data = await response.json();
      setLeads(data.leads || []);
      
      // Auto-selection logic
      if (data.leads && data.leads.length > 0) {
        if (targetIdToSelect) {
          setSelectedLeadId(targetIdToSelect);
        } else if (selectFirst && !selectedLeadId) {
          setSelectedLeadId(data.leads[0].id);
        }
      } else {
        setSelectedLeadId(null);
        setSelectedLead(null);
      }
    } catch (err: unknown) {
      console.error('Error loading leads:', err);
      const message = err instanceof Error ? err.message : 'Error connecting to database';
      setError(message);
    } finally {
      setLoading(false);
    }
  }, [selectedLeadId]);

  // Load details for the selected lead (messages, activities, fresh profile)
  const fetchLeadDetails = useCallback(async (id: string) => {
    try {
      const response = await fetch(`/api/leads/${id}`);
      if (!response.ok) throw new Error('Failed to load lead details');
      const data = await response.json();
      setSelectedLead(data.lead);
      setMessages(data.messages || []);
      setActivities(data.activities || []);
    } catch (err: unknown) {
      console.error('Error loading lead details:', err);
    }
  }, []);

  // Initial fetch on mount
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const selectId = params.get('select');
    fetchLeads(true, selectId);
    if (selectId) {
      // Clean up URL parameter without refreshing the page
      const newUrl = window.location.pathname;
      window.history.replaceState({}, '', newUrl);
    }
  }, [fetchLeads]);

  // Re-fetch lead details whenever the selected lead ID changes
  useEffect(() => {
    if (selectedLeadId) {
      fetchLeadDetails(selectedLeadId);
    } else {
      setSelectedLead(null);
      setMessages([]);
      setActivities([]);
    }
  }, [selectedLeadId, fetchLeadDetails]);

  // Handle stage updates from the dropdown
  const handleUpdateStage = async (leadId: string, stage: LeadStage) => {
    try {
      const response = await fetch(`/api/leads/${leadId}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ lead_stage: stage }),
      });
      if (!response.ok) throw new Error('Failed to update stage');
      
      // Refresh list to update stats and stage display
      await fetchLeads();
      // Refresh details to show new stage and updated activities log
      if (selectedLeadId === leadId) {
        await fetchLeadDetails(leadId);
      }
    } catch (err) {
      console.error('Error updating stage:', err);
      alert('Error updating lead stage');
    }
  };

  // Handle human review toggle/override
  const handleToggleHumanReview = async (leadId: string, needsReview: boolean) => {
    try {
      const response = await fetch(`/api/leads/${leadId}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ needs_human_review: needsReview }),
      });
      if (!response.ok) throw new Error('Failed to toggle human review');
      
      await fetchLeads();
      if (selectedLeadId === leadId) {
        await fetchLeadDetails(leadId);
      }
    } catch (err) {
      console.error('Error toggling review:', err);
      alert('Error updating human review status');
    }
  };

  // Callback when simulator sends a message and API route responds
  const handleMessageProcessed = async () => {
    // Refresh leads list to update scores, stats, status
    await fetchLeads(false, selectedLeadId);
    // Refresh current lead details to load new messages and activities log
    if (selectedLeadId) {
      await fetchLeadDetails(selectedLeadId);
    }
  };

  // Submit manual lead creation form
  const handleCreateLeadSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!newPhone.trim()) return;

    try {
      const response = await fetch('/api/leads', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          phone: newPhone,
          name: newName || null,
          leadSource: newSource,
        }),
      });

      if (!response.ok) {
        const errorData = await response.json();
        throw new Error(errorData.error || 'Failed to create lead');
      }

      const data = await response.json();
      
      // Reset form states
      setNewPhone('');
      setNewName('');
      setNewSource('WhatsApp');
      setIsCreatingLead(false);

      // Refresh data and select the newly created lead
      await fetchLeads(false, data.lead.id);
      
      // Switch view tab to Leads console to interact immediately
    } catch (err: unknown) {
      console.error(err);
      const message = err instanceof Error ? err.message : 'Error creating lead. Ensure phone is unique.';
      alert(message);
    }
  };

  // Count active human review flags for sidebar badge
  const reviewQueueCount = leads.filter(l => l.needs_human_review).length;

  return (
    <div className="flex bg-slate-950 min-h-screen text-slate-100 font-sans">
      {/* 1. Left Sidebar */}
      <Sidebar
        currentTab={currentTab}
        setCurrentTab={setCurrentTab}
        reviewCount={reviewQueueCount}
      />

      {/* Main Content Area */}
      <main className="flex-1 flex flex-col min-w-0 h-screen overflow-hidden">
        {/* Sandbox indicator banner */}
        <SetupBanner />

        {/* Global Dashboard Header */}
        <header className="px-6 py-4 bg-slate-900 border-b border-slate-800 flex items-center justify-between shrink-0">
          <div>
            <h2 className="text-lg font-black text-white capitalize">
              {currentTab === 'dashboard' ? 'Overview Analytics' : currentTab === 'leads' ? 'Lead Qualification Console' : 'Human Review Queue'}
            </h2>
            <p className="text-[10px] text-slate-400 mt-0.5">
              {currentTab === 'dashboard'
                ? 'High-level real-time KPI metrics and conversion funnel logs'
                : 'Interact with leads using the WhatsApp simulator, review details, and manage scores'}
            </p>
          </div>

          <div className="flex gap-2">
            <button
              onClick={() => fetchLeads()}
              className="bg-slate-800 hover:bg-slate-750 text-slate-200 border border-slate-700/50 hover:border-slate-650 px-3 py-1.5 rounded-lg text-xs font-semibold transition-colors flex items-center gap-1.5 cursor-pointer"
            >
              🔄 Refresh
            </button>
            <button
              onClick={handleResetDemo}
              disabled={isResetting}
              className="bg-slate-800 hover:bg-slate-750 text-amber-400 hover:text-amber-300 border border-slate-700/50 hover:border-slate-650 px-3 py-1.5 rounded-lg text-xs font-semibold transition-colors flex items-center gap-1.5 disabled:opacity-50 cursor-pointer"
              title="Reset sandbox data"
            >
              ⚡ Reset Demo
            </button>
            <button
              onClick={() => setShowPlaybook(!showPlaybook)}
              className={`px-3 py-1.5 rounded-lg text-xs font-semibold transition-colors flex items-center gap-1.5 border cursor-pointer ${
                showPlaybook
                  ? 'bg-purple-950/60 text-purple-300 border-purple-800/80 glow-purple'
                  : 'bg-slate-800 hover:bg-slate-750 text-slate-300 border-slate-700/50 hover:border-slate-650'
              }`}
            >
              🔮 Playbook
            </button>
            <button
              onClick={() => setIsCreatingLead(true)}
              className="bg-emerald-600 hover:bg-emerald-500 text-white px-3 py-1.5 rounded-lg text-xs font-semibold transition-colors flex items-center gap-1.5 glow-emerald cursor-pointer"
            >
              ➕ Create Lead
            </button>
          </div>
        </header>

        {/* Main Tab Render Grid */}
        <div className="flex-1 overflow-y-auto p-6">
          {error && (
            <div className="bg-red-950/30 border border-red-900/60 p-4 rounded-xl text-red-400 text-xs mb-6 flex justify-between items-center">
              <span>⚠️ <strong>Error:</strong> {error}</span>
              <button onClick={() => setError(null)} className="font-bold">&times;</button>
            </div>
          )}

          {showPlaybook && (
            <div className="bg-[#181227]/75 border border-purple-900/40 p-5 rounded-xl shadow-2xl mb-6 relative overflow-hidden glow-purple animate-fade-in">
              <div className="absolute top-3 right-3">
                <button
                  onClick={() => setShowPlaybook(false)}
                  className="text-purple-400 hover:text-purple-200 font-bold text-base cursor-pointer"
                  title="Close playbook"
                >
                  &times;
                </button>
              </div>
              
              <div className="flex items-start gap-4">
                <div className="w-9 h-9 rounded-lg bg-purple-900/60 border border-purple-700/40 flex items-center justify-center text-purple-300 font-extrabold text-base shrink-0">
                  🔮
                </div>
                <div className="w-full">
                  <h3 className="text-xs font-bold text-purple-200 uppercase tracking-widest">Apex Realty AI &bull; Interactive 2-Minute Demo Playbook</h3>
                  <p className="text-[10px] text-purple-300/80 mt-1 leading-relaxed">
                    Perform these steps sequentially to present the core value propositions of the platform to agency clients.
                  </p>
                  
                  <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-5 gap-3 mt-4">
                    <div className="bg-[#0e0a1a]/60 border border-purple-950/50 p-3 rounded-lg flex flex-col justify-between">
                      <div>
                        <span className="text-[9px] font-bold text-purple-400 uppercase tracking-widest block mb-1">1. Lead Capture</span>
                        <p className="text-[9px] text-slate-300 leading-normal">
                          Open the <a href="/intake" target="_blank" className="text-purple-400 hover:underline font-bold">Intake Form</a> in a new tab. Submit a request as a new buyer (e.g. <i>"I am Naveen. Budget is 8 crore for 5BHK villa in ECR..."</i>).
                        </p>
                      </div>
                      <span className="text-[8px] text-emerald-400 font-semibold mt-2 block">✓ Automatic Capture</span>
                    </div>

                    <div className="bg-[#0e0a1a]/60 border border-purple-950/50 p-3 rounded-lg flex flex-col justify-between">
                      <div>
                        <span className="text-[9px] font-bold text-purple-400 uppercase tracking-widest block mb-1">2. AI Extraction</span>
                        <p className="text-[9px] text-slate-300 leading-normal">
                          Observe how you are redirected and the new lead is auto-selected. The AI instantly extracts the <b>Property Type</b>, <b>Location</b>, <b>Budget</b>, and <b>Timeline</b>.
                        </p>
                      </div>
                      <span className="text-[8px] text-emerald-400 font-semibold mt-2 block">✓ Zero Manual Data Entry</span>
                    </div>

                    <div className="bg-[#0e0a1a]/60 border border-purple-950/50 p-3 rounded-lg flex flex-col justify-between">
                      <div>
                        <span className="text-[9px] font-bold text-purple-400 uppercase tracking-widest block mb-1">3. Engagement Score</span>
                        <p className="text-[9px] text-slate-300 leading-normal">
                          Point out the calculated <b>Engagement Score (0-100)</b> and <b>Warm/Hot</b> status. Leads scoring $\ge 75$ automatically transition to the <b>Qualified</b> stage.
                        </p>
                      </div>
                      <span className="text-[8px] text-emerald-400 font-semibold mt-2 block">✓ Filter Hot Buyers</span>
                    </div>

                    <div className="bg-[#0e0a1a]/60 border border-purple-950/50 p-3 rounded-lg flex flex-col justify-between">
                      <div>
                        <span className="text-[9px] font-bold text-purple-400 uppercase tracking-widest block mb-1">4. Review Escalation</span>
                        <p className="text-[9px] text-slate-300 leading-normal">
                          In the WhatsApp Simulator, type: <i>"I want to discuss price discounts with a human manager"</i>. Show the client how it flags the review badge instantly.
                        </p>
                      </div>
                      <span className="text-[8px] text-emerald-400 font-semibold mt-2 block">✓ Human Agent Handoff</span>
                    </div>

                    <div className="bg-[#0e0a1a]/60 border border-purple-950/50 p-3 rounded-lg flex flex-col justify-between">
                      <div>
                        <span className="text-[9px] font-bold text-purple-400 uppercase tracking-widest block mb-1">5. Live Analytics</span>
                        <p className="text-[9px] text-slate-300 leading-normal">
                          Switch to the <b>Overview</b> statistics tab. Walk through metrics like average response speeds, source ratios, and stage distribution charts.
                        </p>
                      </div>
                      <span className="text-[8px] text-emerald-400 font-semibold mt-2 block">✓ Conversion Tracking</span>
                    </div>
                  </div>

                  <div className="mt-4 flex gap-3 items-center">
                    <button
                      onClick={handleResetDemo}
                      disabled={isResetting}
                      className="bg-amber-600 hover:bg-amber-500 disabled:opacity-50 text-white font-bold text-[9px] px-3 py-1.5 rounded transition-all shadow cursor-pointer"
                    >
                      {isResetting ? 'Resetting...' : '⚡ Reset Demo Leads'}
                    </button>
                    <span className="text-[8px] text-purple-300/60 font-mono">
                      Wipes the database and loads the 5 default high-fidelity demo leads.
                    </span>
                  </div>
                </div>
              </div>
            </div>
          )}

          {loading ? (
            <div className="h-96 flex items-center justify-center">
              <div className="flex flex-col items-center gap-3">
                <div className="w-10 h-10 border-4 border-slate-800 border-t-emerald-500 rounded-full animate-spin"></div>
                <span className="text-xs text-slate-400 font-medium">Bootstrapping metrics...</span>
              </div>
            </div>
          ) : currentTab === 'dashboard' ? (
            // 2. Overview Statistics view
            <DashboardStats leads={leads} />
          ) : (
            // 3. Lead Qualification Split Console view (Leads or Review Queue)
            <div className="grid grid-cols-1 lg:grid-cols-12 gap-6 h-[calc(100vh-160px)] min-h-[500px]">
              
              {/* Column 1: Leads List (4 spans) */}
              <div className="lg:col-span-4 min-h-0">
                <LeadList
                  leads={leads}
                  selectedLeadId={selectedLeadId}
                  onSelectLead={setSelectedLeadId}
                  filterMode={currentTab === 'review' ? 'review' : 'all'}
                />
              </div>

              {/* Column 2 & 3: Details + Simulator (8 spans) */}
              <div className="lg:col-span-8 grid grid-cols-1 md:grid-cols-2 gap-6 min-h-0">
                {selectedLeadId && selectedLead ? (
                  <>
                    {/* Lead details card */}
                    <div className="min-h-0">
                      <LeadDetail
                        lead={selectedLead}
                        activities={activities}
                        onUpdateStage={handleUpdateStage}
                        onToggleHumanReview={handleToggleHumanReview}
                        messagesCount={messages.length}
                      />
                    </div>

                    {/* Chat simulator panel */}
                    <div className="min-h-0">
                      <ChatSimulator
                        lead={selectedLead}
                        messages={messages}
                        onMessageProcessed={handleMessageProcessed}
                      />
                    </div>
                  </>
                ) : (
                  // Empty state when no lead selected
                  <div className="col-span-2 bg-slate-900 border border-slate-800 rounded-xl p-8 flex flex-col items-center justify-center text-center text-slate-500 shadow-xl h-full">
                    <span className="text-4xl mb-3">👥</span>
                    <h3 className="font-black text-slate-300 text-sm">No Lead Selected</h3>
                    <p className="text-xs text-slate-500 mt-2 max-w-[280px]">
                      Select a lead from the console list on the left to inspect requirements, review system audits, and open the WhatsApp simulator.
                    </p>
                  </div>
                )}
              </div>
            </div>
          )}
        </div>
      </main>

      {/* Manual Create Lead Modal Dialog */}
      {isCreatingLead && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/75 backdrop-blur-sm p-4">
          <div className="bg-slate-900 border border-slate-800 rounded-xl max-w-md w-full p-6 shadow-2xl glow-emerald">
            <div className="flex justify-between items-center mb-5 pb-2 border-b border-slate-800">
              <h3 className="text-sm font-bold text-white uppercase tracking-wider">Create New Lead Channel</h3>
              <button
                onClick={() => setIsCreatingLead(false)}
                className="text-slate-400 hover:text-white text-lg font-bold"
              >
                &times;
              </button>
            </div>

            <form onSubmit={handleCreateLeadSubmit} className="space-y-4">
              <div>
                <label className="block text-[10px] font-bold text-slate-400 uppercase tracking-wider mb-1.5">
                  Phone Number (Required & Unique)
                </label>
                <input
                  type="text"
                  placeholder="+91 99999 88888"
                  value={newPhone}
                  onChange={e => setNewPhone(e.target.value)}
                  className="w-full bg-slate-950 border border-slate-800 rounded-lg py-2 px-3 text-xs text-slate-200 focus:outline-none focus:border-emerald-500 transition-colors"
                  required
                />
              </div>

              <div>
                <label className="block text-[10px] font-bold text-slate-400 uppercase tracking-wider mb-1.5">
                  Lead Full Name (Optional)
                </label>
                <input
                  type="text"
                  placeholder="e.g. Ramesh Babu"
                  value={newName}
                  onChange={e => setNewName(e.target.value)}
                  className="w-full bg-slate-950 border border-slate-800 rounded-lg py-2 px-3 text-xs text-slate-200 focus:outline-none focus:border-emerald-500 transition-colors"
                />
              </div>

              <div>
                <label className="block text-[10px] font-bold text-slate-400 uppercase tracking-wider mb-1.5">
                  Traffic Source Channel
                </label>
                <select
                  value={newSource}
                  onChange={e => setNewSource(e.target.value as LeadSource)}
                  className="w-full bg-slate-950 border border-slate-800 rounded-lg py-2 px-3 text-xs text-slate-200 focus:outline-none focus:border-emerald-500 transition-colors"
                >
                  <option value="WhatsApp">WhatsApp</option>
                  <option value="Facebook">Facebook</option>
                  <option value="Instagram">Instagram</option>
                  <option value="Google Ads">Google Ads</option>
                  <option value="MagicBricks">MagicBricks</option>
                  <option value="99acres">99acres</option>
                  <option value="Housing">Housing</option>
                  <option value="Website">Website</option>
                  <option value="Other">Other</option>
                </select>
              </div>

              <div className="pt-3 flex justify-end gap-3">
                <button
                  type="button"
                  onClick={() => setIsCreatingLead(false)}
                  className="bg-transparent hover:bg-slate-800 border border-slate-800 px-4 py-2 rounded-lg text-xs font-semibold text-slate-300 transition-all"
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  className="bg-emerald-600 hover:bg-emerald-500 text-white px-4 py-2 rounded-lg text-xs font-semibold transition-all glow-emerald"
                >
                  Create & Select
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* Floating Success/Error Toast */}
      {toast && (
        <div className={`fixed bottom-5 right-5 z-50 p-4 rounded-xl border shadow-2xl transition-all duration-300 flex items-start gap-3 max-w-sm ${
          toast.type === 'success'
            ? 'bg-[#0b1b14]/95 border-emerald-800/80 text-emerald-300 glow-emerald animate-fade-in'
            : 'bg-[#1b0b0b]/95 border-red-800/80 text-red-300 glow-red animate-fade-in'
        }`}>
          <div className="text-sm mt-0.5 select-none">
            {toast.type === 'success' ? '✅' : '⚠️'}
          </div>
          <div className="flex-1 min-w-0">
            <h4 className="text-xs font-bold text-white leading-tight">{toast.message}</h4>
            <p className="text-[10px] text-slate-400 mt-1 leading-normal break-words">{toast.subMessage}</p>
          </div>
          <button
            onClick={() => setToast(null)}
            className="text-slate-500 hover:text-white font-bold text-xs ml-2 self-start cursor-pointer transition-colors"
            title="Dismiss"
          >
            &times;
          </button>
        </div>
      )}
    </div>
  );
}

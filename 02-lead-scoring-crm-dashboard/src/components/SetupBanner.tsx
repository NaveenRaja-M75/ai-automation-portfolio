'use client';

import React, { useState, useEffect } from 'react';

export default function SetupBanner() {
  const [isOpen, setIsOpen] = useState(false);
  const [status, setStatus] = useState<{
    supabaseConnected: boolean;
    geminiConnected: boolean;
    currentMode: string;
    databaseType: string;
    geminiStatus: string;
  } | null>(null);

  useEffect(() => {
    async function checkStatus() {
      try {
        const res = await fetch('/api/status');
        if (res.ok) {
          const data = await res.json();
          setStatus(data);
        }
      } catch (err) {
        console.error('Failed to retrieve connection status:', err);
      }
    }
    checkStatus();
    // Refresh status every 10 seconds in case they change something
    const interval = setInterval(checkStatus, 10000);
    return () => clearInterval(interval);
  }, []);

  if (!status) return null;

  const isProduction = status.currentMode === 'Production';

  return (
    <div className="w-full">
      {isProduction ? (
        <div className="bg-emerald-950/40 border-b border-emerald-800/40 px-4 py-2 flex items-center justify-between text-xs text-emerald-300 animate-fade-in">
          <div className="flex items-center gap-2">
            <span className="flex h-2 w-2 relative">
              <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-emerald-400 opacity-75"></span>
              <span className="relative inline-flex rounded-full h-2 w-2 bg-emerald-500"></span>
            </span>
            <span>
              <strong>Production Mode Active:</strong> Live Database & Gemini AI integration connected.
            </span>
          </div>
          <div className="flex gap-4 items-center">
            <span>Supabase Connected: <strong>Yes</strong></span>
            <span className="h-3 w-px bg-emerald-800"></span>
            <span>Gemini Connected: <strong>Yes</strong></span>
            <span className="h-3 w-px bg-emerald-800"></span>
            <span className="text-[10px] bg-emerald-900/40 border border-emerald-800 px-2 py-0.5 rounded font-semibold text-emerald-200">
              PROD
            </span>
          </div>
        </div>
      ) : (
        <div className="bg-amber-950/40 border-b border-amber-800/40 px-4 py-2 flex items-center justify-between text-xs text-amber-300">
          <div className="flex items-center gap-2">
            <span className="flex h-2 w-2 relative">
              <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-amber-400 opacity-75"></span>
              <span className="relative inline-flex rounded-full h-2 w-2 bg-amber-500"></span>
            </span>
            <span>
              <strong>Sandbox Mode Active:</strong> Running on mock database and local AI fallback.
            </span>
          </div>
          <div className="flex gap-4 items-center">
            <span>Supabase Connected: <strong>{status.supabaseConnected ? 'Yes' : 'No'}</strong></span>
            <span className="h-3 w-px bg-amber-800"></span>
            <span>Gemini Connected: <strong>{status.geminiConnected ? 'Yes' : 'No'}</strong></span>
            <span className="h-3 w-px bg-amber-800"></span>
            <button
              onClick={() => setIsOpen(true)}
              className="text-amber-200 hover:text-white underline font-semibold transition-colors"
            >
              How to connect?
            </button>
          </div>
        </div>
      )}

      {isOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm p-4">
          <div className="bg-slate-900 border border-slate-800 rounded-xl max-w-lg w-full p-6 glow-amber overflow-hidden">
            <div className="flex justify-between items-start mb-4">
              <h3 className="text-lg font-bold text-amber-400">Connecting Supabase & Gemini API</h3>
              <button
                onClick={() => setIsOpen(false)}
                className="text-slate-400 hover:text-white text-lg font-bold"
              >
                &times;
              </button>
            </div>
            
            <p className="text-slate-300 text-sm mb-4 leading-relaxed">
              This system is fully wired for production. To connect your live cloud database and official Gemini language models, follow these steps:
            </p>

            <div className="space-y-4">
              <div>
                <span className="text-xs font-semibold text-slate-400 uppercase tracking-wider">Step 1: Create local env file</span>
                <p className="text-xs text-slate-300 mt-1">
                  Create a file named <code className="bg-slate-950 px-1 py-0.5 rounded text-amber-200">.env.local</code> in the project root folder.
                </p>
              </div>

              <div>
                <span className="text-xs font-semibold text-slate-400 uppercase tracking-wider">Step 2: Add credentials</span>
                <pre className="bg-slate-950 p-3 rounded-lg text-xs text-slate-300 font-mono mt-1 overflow-x-auto">
{`NEXT_PUBLIC_SUPABASE_URL=your_supabase_project_url
NEXT_PUBLIC_SUPABASE_ANON_KEY=your_supabase_anon_key
GEMINI_API_KEY=your_gemini_api_key`}
                </pre>
              </div>

              <div>
                <span className="text-xs font-semibold text-slate-400 uppercase tracking-wider">Step 3: Setup tables</span>
                <p className="text-xs text-slate-300 mt-1">
                  Copy the SQL definitions from <code className="bg-slate-950 px-1 py-0.5 rounded text-amber-200">supabase/schema.sql</code> and execute them in your Supabase project&apos;s SQL Editor.
                </p>
              </div>
            </div>

            <div className="mt-6 flex justify-end">
              <button
                onClick={() => setIsOpen(false)}
                className="bg-amber-600 hover:bg-amber-500 text-white font-medium text-sm px-4 py-2 rounded-lg transition-all"
              >
                Close Details
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

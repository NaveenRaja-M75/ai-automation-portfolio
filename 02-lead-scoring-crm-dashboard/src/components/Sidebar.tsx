'use client';

import React from 'react';

interface SidebarProps {
  currentTab: string;
  setCurrentTab: (tab: string) => void;
  reviewCount: number;
}

export default function Sidebar({ currentTab, setCurrentTab, reviewCount }: SidebarProps) {
  const menuItems = [
    { id: 'dashboard', label: 'Overview', icon: '📊' },
    { id: 'leads', label: 'Lead Console', icon: '👥' },
    { id: 'review', label: 'Review Queue', icon: '⚠️', badge: reviewCount },
  ];

  return (
    <aside className="w-64 bg-slate-900 border-r border-slate-800 flex flex-col justify-between shrink-0 h-screen sticky top-0">
      <div>
        {/* Branding header */}
        <div className="p-6 border-b border-slate-800">
          <div className="flex items-center gap-3">
            <div className="w-9 h-9 rounded-lg bg-emerald-500 flex items-center justify-center text-white font-extrabold text-lg glow-emerald">
              A
            </div>
            <div>
              <h1 className="font-extrabold text-sm text-slate-100 tracking-wider">APEX REALTY AI</h1>
              <span className="text-[10px] text-emerald-400 font-mono tracking-widest uppercase">Lead Qualifier</span>
            </div>
          </div>
        </div>

        {/* Navigation items */}
        <nav className="p-4 space-y-1">
          {menuItems.map(item => {
            const isActive = currentTab === item.id;
            return (
              <button
                key={item.id}
                onClick={() => setCurrentTab(item.id)}
                className={`w-full flex items-center justify-between px-4 py-3 rounded-lg text-sm transition-all duration-200 ${
                  isActive
                    ? 'bg-slate-800 text-white font-medium shadow-inner border border-slate-700/50'
                    : 'text-slate-400 hover:text-slate-200 hover:bg-slate-800/40'
                }`}
              >
                <div className="flex items-center gap-3">
                  <span className="text-base">{item.icon}</span>
                  <span>{item.label}</span>
                </div>
                {item.badge !== undefined && item.badge > 0 && (
                  <span className="bg-red-500 text-white text-[10px] font-bold px-2 py-0.5 rounded-full animate-pulse">
                    {item.badge}
                  </span>
                )}
              </button>
            );
          })}
        </nav>
      </div>

      {/* Footer status */}
      <div className="p-4 border-t border-slate-800 bg-slate-950/40 text-center">
        <div className="flex items-center justify-center gap-2 text-xs text-slate-500">
          <div className="w-1.5 h-1.5 rounded-full bg-emerald-500 animate-pulse"></div>
          <span>System Engine Active</span>
        </div>
        <p className="text-[9px] text-slate-600 mt-1">v1.0.0 (Phase 1 MVP)</p>
      </div>
    </aside>
  );
}

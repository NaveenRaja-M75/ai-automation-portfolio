"use client";

import { Save } from "lucide-react";

export default function SettingsPage() {
  return (
    <div className="max-w-3xl mx-auto space-y-8">
      <header>
        <h1 className="text-3xl font-display font-bold text-foreground">Creator Profile Memory</h1>
        <p className="text-muted-foreground mt-2">Configure your baseline style. The Context Compiler will inject this into every workflow.</p>
      </header>

      <div className="bg-[#0a0a0a] border border-border rounded-xl p-8 shadow-xl">
        <form className="space-y-6">
          <div className="space-y-4">
            <div>
              <label className="block text-sm font-medium text-foreground mb-1">Preferred Storytelling Style</label>
              <select className="w-full bg-background border border-border rounded-md px-4 py-2 text-sm focus:ring-1 focus:ring-primary outline-none">
                <option>Documentary (Deep Dives)</option>
                <option>High-Retention Fast Cut</option>
                <option>Story-Driven Educational</option>
              </select>
            </div>
            
            <div>
              <label className="block text-sm font-medium text-foreground mb-1">Base Emotional Intensity</label>
              <input type="range" min="1" max="10" className="w-full accent-primary" />
              <div className="flex justify-between text-xs text-muted-foreground mt-1">
                <span>Analytical (1)</span>
                <span>Dramatic (10)</span>
              </div>
            </div>

            <div>
              <label className="block text-sm font-medium text-foreground mb-1">Visual Direction</label>
              <textarea rows={3} placeholder="e.g. Dark, cinematic lighting, 3D motion graphics..." className="w-full bg-background border border-border rounded-md px-4 py-2 focus:ring-1 focus:ring-primary outline-none text-sm" />
            </div>
          </div>

          <div className="pt-4 border-t border-border flex justify-end">
            <button type="button" className="flex items-center gap-2 bg-primary text-primary-foreground px-6 py-2 rounded-md font-medium hover:bg-primary/90 transition-colors text-sm">
              <Save className="w-4 h-4" /> Save Creator Profile
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}

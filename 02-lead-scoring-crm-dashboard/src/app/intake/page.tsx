'use client';

import React, { useState } from 'react';
import { useRouter } from 'next/navigation';

export default function IntakePage() {
  const router = useRouter();
  const [name, setName] = useState('');
  const [phone, setPhone] = useState('');
  const [message, setMessage] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!phone.trim() || !message.trim()) {
      setError('Phone number and inquiry details are required.');
      return;
    }

    setSubmitting(true);
    setError(null);

    try {
      const res = await fetch('/api/leads/intake', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          name: name.trim() || null,
          phone: phone.trim(),
          message: message.trim(),
          leadSource: 'Website',
        }),
      });

      if (!res.ok) {
        const data = await res.json();
        throw new Error(data.error || 'Failed to submit form.');
      }

      const data = await res.json();
      
      // Redirect to dashboard, selecting the newly created lead
      if (data.lead && data.lead.id) {
        router.push(`/?select=${data.lead.id}`);
      } else {
        router.push('/');
      }
    } catch (err: unknown) {
      console.error('Error submitting lead intake form:', err);
      const msg = err instanceof Error ? err.message : 'An error occurred during submission.';
      setError(msg);
      setSubmitting(false);
    }
  };

  return (
    <div className="bg-[#0b0f19] min-h-screen text-slate-100 font-sans flex flex-col justify-center items-center px-4 relative overflow-hidden">
      
      {/* Decorative gradient blur background objects */}
      <div className="absolute top-1/4 left-1/4 w-96 h-96 bg-emerald-600/10 rounded-full blur-3xl -z-10 animate-pulse"></div>
      <div className="absolute bottom-1/4 right-1/4 w-96 h-96 bg-blue-600/10 rounded-full blur-3xl -z-10 animate-pulse delay-700"></div>

      <div className="max-w-md w-full glass-panel glow-emerald rounded-2xl p-8 shadow-2xl relative border border-slate-800/80 bg-slate-900/60">
        
        {/* Header */}
        <div className="text-center mb-6">
          <div className="w-12 h-12 rounded-xl bg-gradient-to-tr from-emerald-500 to-teal-400 flex items-center justify-center text-white mx-auto shadow-md mb-3">
            🏢
          </div>
          <h1 className="text-xl font-black tracking-tight text-white">Apex Realty</h1>
          <p className="text-xs text-slate-400 mt-1">Submit your property requirements to instantly match with our AI agent</p>
        </div>

        {/* Error Alert */}
        {error && (
          <div className="mb-5 bg-red-950/40 border border-red-900/50 p-3 rounded-lg text-red-400 text-xs flex items-center justify-between">
            <span>⚠️ {error}</span>
            <button onClick={() => setError(null)} className="font-bold hover:text-white">&times;</button>
          </div>
        )}

        {/* Lead Capture Form */}
        <form onSubmit={handleSubmit} className="space-y-4">
          <div>
            <label className="block text-[10px] font-bold text-slate-400 uppercase tracking-widest mb-1.5">
              Full Name
            </label>
            <input
              type="text"
              placeholder="e.g. Naveen Kumar"
              value={name}
              onChange={e => setName(e.target.value)}
              className="w-full bg-[#070b13] border border-slate-800 rounded-lg py-2.5 px-4 text-xs text-slate-200 placeholder-slate-600 focus:outline-none focus:border-emerald-500 transition-colors focus:ring-1 focus:ring-emerald-500/25"
              disabled={submitting}
            />
          </div>

          <div>
            <label className="block text-[10px] font-bold text-slate-400 uppercase tracking-widest mb-1.5">
              Phone Number
            </label>
            <input
              type="text"
              placeholder="e.g. +91 99999 88888"
              value={phone}
              onChange={e => setPhone(e.target.value)}
              className="w-full bg-[#070b13] border border-slate-800 rounded-lg py-2.5 px-4 text-xs text-slate-200 placeholder-slate-600 focus:outline-none focus:border-emerald-500 transition-colors focus:ring-1 focus:ring-emerald-500/25"
              required
              disabled={submitting}
            />
          </div>

          <div>
            <label className="block text-[10px] font-bold text-slate-400 uppercase tracking-widest mb-1.5">
              Inquiry / Property Preferences
            </label>
            <textarea
              placeholder="Describe what you are looking for (e.g. 5BHK luxury villa in ECR, budget 8 crore, purchase timeline 6 months)"
              value={message}
              onChange={e => setMessage(e.target.value)}
              rows={4}
              className="w-full bg-[#070b13] border border-slate-800 rounded-lg py-2.5 px-4 text-xs text-slate-200 placeholder-slate-600 focus:outline-none focus:border-emerald-500 transition-colors focus:ring-1 focus:ring-emerald-500/25 resize-none leading-relaxed"
              required
              disabled={submitting}
            ></textarea>
          </div>

          <button
            type="submit"
            disabled={submitting}
            className="w-full bg-emerald-600 hover:bg-emerald-500 text-white font-bold text-xs py-3 rounded-lg shadow-lg hover:shadow-emerald-900/20 active:scale-[0.98] transition-all flex items-center justify-center gap-2 disabled:opacity-50"
          >
            {submitting ? (
              <>
                <div className="w-3.5 h-3.5 border-2 border-white/20 border-t-white rounded-full animate-spin"></div>
                Matching Requirements...
              </>
            ) : (
              'Connect with AI Agent'
            )}
          </button>
        </form>
      </div>
      
      {/* Footer copyright */}
      <span className="text-[9px] text-slate-600 tracking-wider uppercase font-mono mt-8">
        &copy; {new Date().getFullYear()} Apex Realty Group. Demo Sandbox.
      </span>

    </div>
  );
}

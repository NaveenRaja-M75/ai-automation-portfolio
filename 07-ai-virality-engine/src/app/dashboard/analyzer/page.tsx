"use client";

import { useState } from "react";
import { Search, Activity, AlertTriangle, TrendingUp } from "lucide-react";
import { motion } from "framer-motion";

export default function AnalyzerPage() {
  const [url, setUrl] = useState("");
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<any>(null);

  const analyzeChannel = async (e: React.FormEvent) => {
    e.preventDefault();
    setLoading(true);
    setResult(null);
    try {
      const res = await fetch("/api/analyzer", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ url })
      });
      const data = await res.json();
      if (data.success) {
        setResult(data.data);
      } else {
        alert("Error: " + data.error);
      }
    } catch (err) {
      alert("Failed to analyze");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="max-w-5xl mx-auto space-y-8 pb-20">
      <header>
        <h1 className="text-3xl font-display font-bold text-foreground">Channel Analyzer</h1>
        <p className="text-muted-foreground mt-2">Decode the retention psychology and viral patterns of any channel.</p>
      </header>

      <form onSubmit={analyzeChannel} className="bg-[#0a0a0a] border border-border rounded-xl p-6 shadow-xl flex gap-4">
        <div className="flex-1 relative">
          <Search className="w-5 h-5 absolute left-4 top-1/2 -translate-y-1/2 text-muted-foreground" />
          <input 
            type="url" 
            required 
            value={url}
            onChange={e => setUrl(e.target.value)}
            placeholder="Paste YouTube Channel URL..." 
            className="w-full bg-background border border-border rounded-lg pl-12 pr-4 py-3 focus:ring-1 focus:ring-primary outline-none transition-colors" 
          />
        </div>
        <button 
          type="submit" 
          disabled={loading}
          className="bg-primary text-primary-foreground px-8 py-3 rounded-lg font-medium hover:bg-primary/90 transition-colors disabled:opacity-50"
        >
          {loading ? "Analyzing..." : "Analyze"}
        </button>
      </form>

      {loading && (
        <div className="py-20 flex flex-col items-center space-y-4">
          <Activity className="w-10 h-10 text-primary animate-pulse" />
          <p className="text-muted-foreground font-mono">Extracting semantic patterns & calculating scores...</p>
        </div>
      )}

      {result && (
        <motion.div 
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          className="space-y-8"
        >
          {/* Structured Scores */}
          <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
            <ScoreCard title="Hook Curiosity" score={result.scores.hookCuriosity} />
            <ScoreCard title="Emotional Intensity" score={result.scores.emotionalIntensity} />
            <ScoreCard title="Retention Risk" score={result.scores.retentionRisk} inverse />
            <ScoreCard title="Narrative Strength" score={result.scores.narrativeStrength} />
          </div>

          <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
            <div className="bg-[#0a0a0a] border border-border rounded-xl p-6 space-y-4">
              <h3 className="text-lg font-semibold flex items-center gap-2"><TrendingUp className="text-green-500 w-5 h-5"/> Viral Patterns</h3>
              <ul className="space-y-3">
                {result.viralPatterns.map((pattern: string, i: number) => (
                  <li key={i} className="text-muted-foreground text-sm flex gap-2">
                    <span className="text-primary mt-0.5">•</span>
                    {pattern}
                  </li>
                ))}
              </ul>
            </div>
            
            <div className="bg-[#0a0a0a] border border-border rounded-xl p-6 space-y-4">
              <h3 className="text-lg font-semibold flex items-center gap-2"><AlertTriangle className="text-yellow-500 w-5 h-5"/> Weaknesses & Risks</h3>
              <ul className="space-y-3">
                {result.weaknesses.map((weakness: string, i: number) => (
                  <li key={i} className="text-muted-foreground text-sm flex gap-2">
                    <span className="text-yellow-500 mt-0.5">•</span>
                    {weakness}
                  </li>
                ))}
              </ul>
            </div>
          </div>
        </motion.div>
      )}
    </div>
  );
}

function ScoreCard({ title, score, inverse = false }: { title: string, score: number, inverse?: boolean }) {
  // Assuming score is 1-100
  const getColor = () => {
    if (inverse) {
      if (score > 70) return "text-red-500";
      if (score > 40) return "text-yellow-500";
      return "text-green-500";
    }
    if (score > 80) return "text-green-500";
    if (score > 60) return "text-yellow-500";
    return "text-red-500";
  };

  return (
    <div className="bg-[#0a0a0a] border border-border rounded-xl p-5 flex flex-col justify-between h-32">
      <h4 className="text-sm font-medium text-muted-foreground">{title}</h4>
      <div className="flex items-end gap-2">
        <span className={`text-4xl font-display font-bold ${getColor()}`}>{score}</span>
        <span className="text-muted-foreground mb-1">/100</span>
      </div>
    </div>
  );
}

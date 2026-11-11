"use client";

import { useState } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { ArrowRight, Sparkles, RefreshCcw } from "lucide-react";

export default function ScriptEnginePage() {
  const [step, setStep] = useState(1);
  const [loadingMsg, setLoadingMsg] = useState("Initializing Context Compiler...");
  const [result, setResult] = useState<any>(null);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  
  const [formData, setFormData] = useState({
    niche: "",
    topic: "",
    targetAudience: "",
    tone: "Cinematic & Dramatic",
    pacing: "Aggressive Hook, Fast Body"
  });

  const generateScript = async () => {
    setStep(3);
    setErrorMsg(null);
    
    // Simulate progression of agents for UX
    const messages = [
      "Agent 0: Compiling Creator Context...",
      "Agent 1: Analyzing Topic & Psychology...",
      "Agent 2: Generating Elite Hooks...",
      "Agent 3: Mapping Emotional Flow...",
      "Agent 4: Drafting Cinematic Script...",
      "Agent 5: Refining Retention & Pacing..."
    ];
    let msgIndex = 0;
    const interval = setInterval(() => {
      msgIndex = Math.min(msgIndex + 1, messages.length - 1);
      setLoadingMsg(messages[msgIndex]);
    }, 2500);

    try {
      const res = await fetch("/api/scripts/generate", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(formData)
      });
      const data = await res.json();
      
      clearInterval(interval);
      if (data.success) {
        setResult(data.data);
        setStep(4);
      } else {
        setErrorMsg("Generation failed: " + data.error);
        setStep(2);
      }
    } catch (err) {
      clearInterval(interval);
      setErrorMsg("Something went wrong while connecting to the AI models. Please try again.");
      setStep(2);
    }
  };
  
  return (
    <div className="max-w-4xl mx-auto space-y-8 pb-20">
      <header>
        <h1 className="text-3xl font-display font-bold text-foreground">AI Viral Script Engine</h1>
        <p className="text-muted-foreground mt-2">Generate cinematic, psychologically optimized scripts via our Multi-Agent Pipeline.</p>
      </header>

      {/* Progress */}
      {step < 4 && (
        <div className="flex items-center space-x-2 text-sm font-medium text-muted-foreground">
          <span className={step >= 1 ? "text-primary" : ""}>1. Core Concept</span>
          <span>→</span>
          <span className={step >= 2 ? "text-primary" : ""}>2. Psychology & Tone</span>
          <span>→</span>
          <span className={step >= 3 ? "text-primary" : ""}>3. Orchestration</span>
        </div>
      )}

      {/* Form / Result Container */}
      <div className="bg-[#0a0a0a] border border-border rounded-xl p-8 shadow-2xl relative overflow-hidden">
        <div className="absolute inset-0 bg-gradient-to-br from-primary/5 to-transparent pointer-events-none" />
        
        <AnimatePresence mode="wait">
          {step === 1 && (
            <motion.div 
              key="step1"
              initial={{ opacity: 0, x: 20 }}
              animate={{ opacity: 1, x: 0 }}
              exit={{ opacity: 0, x: -20 }}
              className="space-y-6 relative z-10"
            >
              <div className="space-y-4">
                <div>
                  <label className="block text-sm font-medium text-foreground mb-1">Niche / Category</label>
                  <input value={formData.niche} onChange={e => setFormData({...formData, niche: e.target.value})} type="text" placeholder="e.g. True Crime, AI News, Finance" className="w-full bg-background border border-border rounded-md px-4 py-2 focus:ring-1 focus:ring-primary outline-none" />
                </div>
                <div>
                  <label className="block text-sm font-medium text-foreground mb-1">Specific Video Topic</label>
                  <textarea value={formData.topic} onChange={e => setFormData({...formData, topic: e.target.value})} rows={3} placeholder="Describe the specific topic or idea..." className="w-full bg-background border border-border rounded-md px-4 py-2 focus:ring-1 focus:ring-primary outline-none" />
                </div>
                <div>
                  <label className="block text-sm font-medium text-foreground mb-1">Target Audience</label>
                  <input value={formData.targetAudience} onChange={e => setFormData({...formData, targetAudience: e.target.value})} type="text" placeholder="e.g. Beginners, Tech Enthusiasts" className="w-full bg-background border border-border rounded-md px-4 py-2 focus:ring-1 focus:ring-primary outline-none" />
                </div>
              </div>
              <div className="flex justify-end pt-4">
                <button onClick={() => setStep(2)} className="flex items-center gap-2 bg-foreground text-background px-6 py-2 rounded-md font-medium hover:bg-foreground/90 transition-colors">
                  Next Step <ArrowRight className="w-4 h-4" />
                </button>
              </div>
            </motion.div>
          )}

          {step === 2 && (
            <motion.div 
              key="step2"
              initial={{ opacity: 0, x: 20 }}
              animate={{ opacity: 1, x: 0 }}
              exit={{ opacity: 0, x: -20 }}
              className="space-y-6 relative z-10"
            >
              {errorMsg && (
                <div className="bg-destructive/10 border border-destructive text-destructive px-4 py-3 rounded-md text-sm">
                  {errorMsg}
                </div>
              )}
              <div className="space-y-4">
                <div className="grid grid-cols-2 gap-4">
                  <div>
                    <label className="block text-sm font-medium text-foreground mb-1">Tone</label>
                    <select value={formData.tone} onChange={e => setFormData({...formData, tone: e.target.value})} className="w-full bg-background border border-border rounded-md px-4 py-2 focus:ring-1 focus:ring-primary outline-none">
                      <option>Cinematic & Dramatic</option>
                      <option>Educational & Analytical</option>
                      <option>High Energy & Fast Paced</option>
                      <option>Dark & Mysterious</option>
                    </select>
                  </div>
                  <div>
                    <label className="block text-sm font-medium text-foreground mb-1">Pacing</label>
                    <select value={formData.pacing} onChange={e => setFormData({...formData, pacing: e.target.value})} className="w-full bg-background border border-border rounded-md px-4 py-2 focus:ring-1 focus:ring-primary outline-none">
                      <option>Aggressive Hook, Fast Body</option>
                      <option>Slow Burn, High Tension</option>
                      <option>Steady Educational</option>
                    </select>
                  </div>
                </div>
              </div>
              <div className="flex justify-between pt-4">
                <button onClick={() => setStep(1)} className="text-muted-foreground hover:text-foreground transition-colors font-medium">
                  Back
                </button>
                <button onClick={generateScript} className="flex items-center gap-2 bg-primary text-primary-foreground px-6 py-2 rounded-md font-medium hover:bg-primary/90 transition-colors shadow-[0_0_15px_rgba(139,92,246,0.5)]">
                  Generate Script <Sparkles className="w-4 h-4" />
                </button>
              </div>
            </motion.div>
          )}

          {step === 3 && (
            <motion.div 
              key="step3"
              initial={{ opacity: 0, scale: 0.95 }}
              animate={{ opacity: 1, scale: 1 }}
              className="py-16 flex flex-col items-center justify-center space-y-6 text-center relative z-10"
            >
              <div className="w-16 h-16 border-4 border-primary/20 border-t-primary rounded-full animate-spin shadow-[0_0_20px_rgba(139,92,246,0.3)]" />
              <div className="space-y-2">
                <h3 className="text-2xl font-medium font-display text-primary">Orchestrating AI Agents</h3>
                <motion.p 
                  key={loadingMsg}
                  initial={{ opacity: 0, y: 5 }}
                  animate={{ opacity: 1, y: 0 }}
                  className="text-muted-foreground font-mono text-sm"
                >
                  {loadingMsg}
                </motion.p>
              </div>
            </motion.div>
          )}

          {step === 4 && result && (
            <motion.div 
              key="step4"
              initial={{ opacity: 0, y: 20 }}
              animate={{ opacity: 1, y: 0 }}
              className="space-y-8 relative z-10"
            >
              <div className="flex items-center justify-between border-b border-border pb-4">
                <div>
                  <h2 className="text-2xl font-bold font-display text-primary">{result.finalScript.finalTitle || "Cinematic Output Generated"}</h2>
                  <p className="text-sm text-muted-foreground mt-1 flex items-center gap-2">
                    <Sparkles className="w-3 h-3 text-accent-foreground" /> Retention-Optimized by 6 Agents
                  </p>
                </div>
                <button onClick={() => setStep(1)} className="text-sm bg-muted text-muted-foreground px-4 py-2 rounded-md hover:text-foreground transition-colors flex items-center gap-2">
                  <RefreshCcw className="w-3 h-3" /> Start Over
                </button>
              </div>

              {/* Hooks Section */}
              <div className="space-y-4">
                <h3 className="text-lg font-semibold text-foreground border-l-2 border-primary pl-3">Selected Elite Hook</h3>
                <div className="bg-background border border-border rounded-lg p-4">
                  <p className="text-sm text-muted-foreground mb-2 italic">Psychology: {result.hooks.hooks?.[0]?.psychologicalTrigger}</p>
                  <p className="text-foreground font-medium text-lg">"{result.hooks.hooks?.[0]?.hookText}"</p>
                </div>
              </div>

              {/* Script Sections */}
              <div className="space-y-4">
                <h3 className="text-lg font-semibold text-foreground border-l-2 border-primary pl-3">Cinematic Script</h3>
                <div className="space-y-4">
                  {result.finalScript.finalSections?.map((section: any, idx: number) => (
                    <div key={idx} className="bg-background border border-border rounded-lg p-5 space-y-3">
                      <div className="flex justify-between items-center border-b border-border/50 pb-2">
                        <h4 className="font-medium text-primary text-sm uppercase tracking-wider">{section.sectionName}</h4>
                      </div>
                      <p className="text-foreground leading-relaxed">
                        {section.narration}
                      </p>
                      <div className="bg-[#050505] rounded p-3 mt-2 text-sm text-muted-foreground font-mono flex flex-col gap-1">
                        <span className="text-accent-foreground flex items-center gap-2">🎥 {section.visualDirection}</span>
                        {section.bRollSuggestion && <span>🎞️ B-Roll: {section.bRollSuggestion}</span>}
                      </div>
                    </div>
                  ))}
                </div>
              </div>
              
              {/* Thumbnail Concepts */}
              <div className="space-y-4">
                <h3 className="text-lg font-semibold text-foreground border-l-2 border-primary pl-3">Thumbnail Psychology</h3>
                <ul className="list-disc pl-5 space-y-2 text-muted-foreground">
                  {result.finalScript.thumbnailConcepts?.map((concept: string, idx: number) => (
                    <li key={idx}>{concept}</li>
                  ))}
                </ul>
              </div>

            </motion.div>
          )}
        </AnimatePresence>
      </div>
    </div>
  );
}

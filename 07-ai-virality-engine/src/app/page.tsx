import Link from "next/link";
import { Sparkles, BrainCircuit, Activity } from "lucide-react";

export default function LandingPage() {
  return (
    <div className="min-h-screen bg-[#050505] text-foreground flex flex-col items-center justify-center relative overflow-hidden">
      {/* Background Glow */}
      <div className="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 w-[800px] h-[800px] bg-primary/20 rounded-full blur-[120px] pointer-events-none" />
      
      <main className="relative z-10 max-w-5xl mx-auto px-6 text-center space-y-10 mt-32">
        <div className="inline-flex items-center gap-2 px-4 py-2 rounded-full border border-primary/30 bg-primary/10 text-primary text-sm font-medium">
          <Sparkles className="w-4 h-4" /> 
          The Elite Creator Intelligence Platform
        </div>
        
        <h1 className="text-5xl md:text-7xl font-display font-bold tracking-tight text-white leading-tight">
          Not another AI wrapper. <br />
          <span className="text-transparent bg-clip-text bg-gradient-to-r from-primary to-accent-foreground">A strategic growth engine.</span>
        </h1>
        
        <p className="text-lg md:text-xl text-muted-foreground max-w-2xl mx-auto leading-relaxed">
          Orchestrate cinematic storytelling, analyze retention psychology, and optimize your channel architecture with a multi-agent AI pipeline built specifically for top-tier creators.
        </p>
        
        <div className="flex items-center justify-center gap-4 pt-4">
          <Link href="/login" className="bg-primary text-primary-foreground px-8 py-4 rounded-xl font-medium text-lg hover:bg-primary/90 transition-all shadow-[0_0_30px_rgba(139,92,246,0.4)]">
            Initialize Engine
          </Link>
          <Link href="#features" className="bg-[#171717] border border-border text-foreground px-8 py-4 rounded-xl font-medium text-lg hover:bg-[#262626] transition-all">
            View Architecture
          </Link>
        </div>
      </main>

      {/* Feature Grid */}
      <div id="features" className="relative z-10 max-w-6xl mx-auto px-6 py-32 grid grid-cols-1 md:grid-cols-2 gap-8 w-full mt-20 border-t border-border/50">
        <div className="bg-background/50 border border-border rounded-2xl p-8 backdrop-blur-sm">
          <BrainCircuit className="w-10 h-10 text-primary mb-4" />
          <h3 className="text-2xl font-bold font-display text-white mb-2">Multi-Agent Script Engine</h3>
          <p className="text-muted-foreground">Go beyond a single prompt. 6 specialized AI agents work in sequence to research, hook, pace, write, and critique your script for maximum retention.</p>
        </div>
        <div className="bg-background/50 border border-border rounded-2xl p-8 backdrop-blur-sm">
          <Activity className="w-10 h-10 text-accent-foreground mb-4" />
          <h3 className="text-2xl font-bold font-display text-white mb-2">Psychological Analyzer</h3>
          <p className="text-muted-foreground">Decode the structural secrets of any channel. We extract hook curiosity, emotional intensity, and retention risk to uncover viral patterns.</p>
        </div>
      </div>
    </div>
  );
}

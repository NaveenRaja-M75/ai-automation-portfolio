import Link from "next/link";
import { LayoutDashboard, Film, Settings, Activity } from "lucide-react";

export default function DashboardLayout({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex h-screen bg-background text-foreground">
      {/* Sidebar */}
      <aside className="w-64 border-r border-border bg-[#0a0a0a] flex flex-col">
        <div className="h-16 flex items-center px-6 border-b border-border">
          <h1 className="font-display font-bold text-lg tracking-wide text-primary">Virality<span className="text-foreground">Engine</span></h1>
        </div>
        <nav className="flex-1 overflow-y-auto py-4 px-3 space-y-1">
          <Link href="/dashboard" className="flex items-center gap-3 px-3 py-2 rounded-md hover:bg-muted transition-colors text-sm font-medium">
            <LayoutDashboard className="w-4 h-4 text-muted-foreground" /> Overview
          </Link>
          <Link href="/dashboard/script-engine" className="flex items-center gap-3 px-3 py-2 rounded-md hover:bg-muted transition-colors text-sm font-medium">
            <Film className="w-4 h-4 text-muted-foreground" /> Script Engine
          </Link>
          <Link href="/dashboard/analyzer" className="flex items-center gap-3 px-3 py-2 rounded-md hover:bg-muted transition-colors text-sm font-medium">
            <Activity className="w-4 h-4 text-muted-foreground" /> Channel Analyzer
          </Link>
        </nav>
        <div className="p-4 border-t border-border">
          <Link href="/dashboard/settings" className="flex items-center gap-3 px-3 py-2 rounded-md hover:bg-muted transition-colors text-sm font-medium">
            <Settings className="w-4 h-4 text-muted-foreground" /> Creator Profile
          </Link>
        </div>
      </aside>
      
      {/* Main Content */}
      <main className="flex-1 overflow-y-auto">
        <div className="p-8">
          {children}
        </div>
      </main>
    </div>
  );
}

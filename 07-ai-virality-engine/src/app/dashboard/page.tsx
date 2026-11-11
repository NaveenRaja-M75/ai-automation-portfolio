export default function DashboardOverviewPage() {
  return (
    <div className="max-w-4xl mx-auto space-y-8">
      <header>
        <h1 className="text-3xl font-display font-bold text-foreground">Dashboard</h1>
        <p className="text-muted-foreground mt-2">Welcome back to your intelligence hub.</p>
      </header>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
        <div className="bg-[#0a0a0a] border border-border rounded-xl p-6">
          <h3 className="font-semibold text-lg text-foreground mb-2">Recent Scripts</h3>
          <p className="text-sm text-muted-foreground">No recent scripts found.</p>
        </div>
        <div className="bg-[#0a0a0a] border border-border rounded-xl p-6">
          <h3 className="font-semibold text-lg text-foreground mb-2">Recent Analyses</h3>
          <p className="text-sm text-muted-foreground">No recent channel analyses.</p>
        </div>
      </div>
    </div>
  );
}

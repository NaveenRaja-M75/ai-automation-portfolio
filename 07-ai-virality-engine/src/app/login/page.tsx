"use client";

import { login, signup } from "./actions";
import { useState } from "react";

export default function LoginPage() {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");

  const handleLogin = async (formData: FormData) => {
    console.log("Log in action triggered with email:", formData.get("email"));
    await login(formData);
  };

  const handleSignup = async (formData: FormData) => {
    console.log("Sign up action triggered with email:", formData.get("email"));
    await signup(formData);
  };

  return (
    <div className="flex h-screen w-full items-center justify-center bg-background">
      <div className="w-full max-w-md space-y-8 px-8 py-10 bg-[#0a0a0a] border border-border rounded-2xl shadow-2xl">
        <div className="text-center">
          <h2 className="text-3xl font-display font-bold text-foreground">AI Virality Engine</h2>
          <p className="mt-2 text-sm text-muted-foreground">Sign in to orchestrate your creator intelligence.</p>
        </div>
        <form className="mt-8 space-y-6">
          <div className="space-y-4">
            <div>
              <label htmlFor="email" className="block text-sm font-medium text-foreground">Email address</label>
              <input 
                id="email" 
                name="email" 
                type="email" 
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                className="mt-1 block w-full rounded-md border border-border bg-background px-3 py-2 text-foreground focus:border-primary focus:outline-none focus:ring-1 focus:ring-primary sm:text-sm transition-colors" 
              />
            </div>
            <div>
              <label htmlFor="password" className="block text-sm font-medium text-foreground">Password</label>
              <input 
                id="password" 
                name="password" 
                type="password" 
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                className="mt-1 block w-full rounded-md border border-border bg-background px-3 py-2 text-foreground focus:border-primary focus:outline-none focus:ring-1 focus:ring-primary sm:text-sm transition-colors" 
              />
            </div>
          </div>
          <div className="flex flex-col space-y-3">
            <button formAction={handleLogin} className="w-full flex justify-center py-2.5 px-4 border border-transparent rounded-md shadow-sm text-sm font-medium text-primary-foreground bg-primary hover:bg-primary/90 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-primary focus:ring-offset-background transition-colors">
              Log in
            </button>
            <button formAction={handleSignup} className="w-full flex justify-center py-2.5 px-4 border border-border rounded-md shadow-sm text-sm font-medium text-foreground bg-transparent hover:bg-muted focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-primary focus:ring-offset-background transition-colors">
              Sign up
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}

import { GoogleGenAI } from "@google/genai";

const apiKey = process.env.GEMINI_API_KEY || "";

// Only instantiate if API key exists, prevents build errors if env var is missing during build
export const ai = new GoogleGenAI({ apiKey });

export async function generateJSON(prompt: string, schema?: any, retries = 3) {
  if (!apiKey) throw new Error("GEMINI_API_KEY is not configured.");
  
  let lastError;
  for (let attempt = 0; attempt < retries; attempt++) {
    try {
      const response = await ai.models.generateContent({
        model: "gemini-2.5-flash",
        contents: prompt,
        config: {
          responseMimeType: "application/json",
          ...(schema && { responseSchema: schema }),
        },
      });
      
      if (!response.text) throw new Error("No text returned from Gemini");
      return JSON.parse(response.text);
    } catch (error: any) {
      lastError = error;
      // Only retry on 503 Unavailable or 429 Too Many Requests
      const status = error?.status || error?.error?.status || error?.response?.status;
      if ((status === 503 || status === 429 || error.message?.includes("503") || error.message?.includes("UNAVAILABLE")) && attempt < retries - 1) {
        console.warn(`Gemini API overloaded. Retrying attempt ${attempt + 1}/${retries}...`);
        await new Promise(resolve => setTimeout(resolve, 2000 * Math.pow(2, attempt)));
      } else {
        throw error;
      }
    }
  }
  throw lastError;
}

export async function generateText(prompt: string) {
  if (!apiKey) throw new Error("GEMINI_API_KEY is not configured.");
  
  const response = await ai.models.generateContent({
    model: "gemini-2.5-flash",
    contents: prompt,
  });
  
  return response.text;
}

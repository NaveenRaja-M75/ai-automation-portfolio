import { GoogleGenerativeAI } from '@google/generative-ai';
import { sanitizeName, normalizeAreaName } from './sanitization';

const apiKey = process.env.GEMINI_API_KEY || '';

// Mask key helper for safe logging
const maskApiKey = (key: string) => {
  if (!key) return 'Not Configured';
  if (key === 'your_gemini_api_key') return 'Placeholder Key Detected';
  if (key.length <= 8) return '********';
  return `${key.slice(0, 4)}...${key.slice(-4)}`;
};

console.log(`[Gemini SDK Config] API Key status: ${maskApiKey(apiKey)}`);

// Initialize Gemini SDK if API key is provided
let genAI: GoogleGenerativeAI | null = null;
if (apiKey && apiKey !== 'your_gemini_api_key') {
  try {
    genAI = new GoogleGenerativeAI(apiKey);
    console.log('[Gemini SDK Config] GoogleGenerativeAI client successfully initialized.');
  } catch (err) {
    console.error('Failed to initialize Gemini AI SDK:', err);
  }
} else {
  console.log('[Gemini SDK Config] Running in Sandbox/Mock AI Fallback mode (no active API key).');
}

export function isGeminiActive(): boolean {
  return genAI !== null;
}

export interface QualificationResult {
  name: string | null;
  budget: string | null;
  preferred_area: string | null;
  property_type: string | null;
  timeline: string | null;
  reply: string;
}

// System instructions to feed to Gemini
const SYSTEM_INSTRUCTION = `
You are a professional, polite, and direct real estate qualification assistant for Apex Realty.
Your job is to converse with a lead via WhatsApp and qualify them by naturally gathering these 5 key requirements:
1. Name (their name)
2. Budget (e.g., "80 Lakhs", "1.5 Crores", "45L")
3. Preferred Area / Location (e.g., "OMR", "Whitefield", "Hebbal")
4. Property Type (e.g., "2BHK", "3BHK Apartment", "Villa", "Plot")
5. Timeline to purchase (e.g., "1 month", "immediate", "3 months", "just browsing")

Rules:
- Analyze the entire conversation history provided.
- Extract any values mentioned by the user and update the fields. If a field is not yet mentioned or clear, keep it null. If a field was previously extracted, retain it unless the user explicitly changes it.
- Generate a friendly, concise, WhatsApp-style response (the 'reply' field) to ask for one or two of the missing details.
- Do NOT ask for everything at once. Keep the conversation flowing naturally.
- Keep the user focused on the real estate purchase. If they ask off-topic questions, polite redirect them back to finding their home.
- You MUST output a JSON response in the following schema:
{
  "name": "extracted name or null",
  "budget": "extracted budget or null",
  "preferred_area": "extracted area or null",
  "property_type": "extracted property type or null",
  "timeline": "extracted timeline or null",
  "reply": "your next friendly message to the customer asking for missing details"
}
`;

// Local Regex-based mock parser for Sandbox/Mock fallback mode
export function runMockGeminiParser(
  history: { sender: 'lead' | 'agent' | 'salesperson'; content: string }[],
  currentData: {
    name: string | null;
    budget: string | null;
    preferred_area: string | null;
    property_type: string | null;
    timeline: string | null;
  }
): QualificationResult {
  const result: QualificationResult = {
    name: currentData.name,
    budget: currentData.budget,
    preferred_area: currentData.preferred_area,
    property_type: currentData.property_type,
    timeline: currentData.timeline,
    reply: '',
  };

  // Analyze the last message from the lead
  const lastLeadMsg = [...history].reverse().find(m => m.sender === 'lead')?.content || '';
  const text = lastLeadMsg.toLowerCase();

  // 1. Try to extract Name (with sanitization checks)
  const nameMatch = lastLeadMsg.match(/(?:my name is|this is|name is|i am|i'm)\s+([A-Za-z]{2,30}(?:\s+[A-Za-z]{2,30})?)/i) ||
                    lastLeadMsg.match(/\b([A-Za-z]{2,30})\s+(?:here|this side)\b/i);
  if (nameMatch) {
    const sanitized = sanitizeName(nameMatch[1].trim());
    if (sanitized) {
      result.name = sanitized;
    }
  }

  // 2. Try to extract Budget
  // Matches like "90 lakhs", "1.2 crores", "55l", "2cr", "50 lakh", "80l"
  const budgetMatch = lastLeadMsg.match(/(\d+(?:\.\d+)?\s*(?:lakh|crore|cr|l|lk|lac|c)s?\b)/i) || 
                      lastLeadMsg.match(/(?:budget|around|under|about)\s+(\d+)\s*(?:lakh|crore|cr|l|lk|lac|c)?s?\b/i) ||
                      lastLeadMsg.match(/\b(\d{6,8})\b/); // Raw numbers like 8000000
  if (budgetMatch) {
    // Avoid matching BHK numbers as budget (e.g. "2 bhk")
    const matchedStr = budgetMatch[1].trim();
    const adjacentBhk = lastLeadMsg.match(new RegExp(`\\b${matchedStr}\\s*bhk`, 'i'));
    if (!adjacentBhk) {
      result.budget = matchedStr;
    }
  }

  // 3. Try to extract Property Type
  const propMatch = lastLeadMsg.match(/(villa|apartment|flat|plot|house|studio|row house|land|\d\s*bhk)/i);
  if (propMatch) {
    const type = propMatch[1].trim().toLowerCase();
    if (type.includes('bhk')) {
      result.property_type = type.replace(/\s+/g, '').toUpperCase();
    } else if (type === 'villa') {
      result.property_type = 'Villa';
    } else if (type === 'plot' || type === 'land') {
      result.property_type = 'Plot';
    } else if (type === 'flat') {
      result.property_type = 'Flat';
    } else if (type === 'apartment') {
      result.property_type = 'Apartment';
    } else {
      result.property_type = type.charAt(0).toUpperCase() + type.slice(1);
    }
  }

  // 4. Try to extract Preferred Area (Gazetteer lookup first, then fallback regex)
  const hubs = ['omr', 'ecr', 'velachery', 'adyar', 'tambaram', 'porur', 'anna nagar', 'annanagar', 'guindy', 'sholinganallur', 'medavakkam', 'perungudi'];
  let foundHub = null;
  for (const hub of hubs) {
    const regex = new RegExp(`\\b${hub}\\b`, 'i');
    if (regex.test(text)) {
      foundHub = hub;
      break;
    }
  }

  if (foundHub) {
    result.preferred_area = normalizeAreaName(foundHub);
  } else {
    const areaMatch = lastLeadMsg.match(/(?:in|at|near|location|area)\s+([A-Za-z]+(?:\s+[A-Za-z]+)?)/i);
    if (areaMatch) {
      const normalized = normalizeAreaName(areaMatch[1].trim());
      if (normalized) {
        result.preferred_area = normalized;
      }
    }
  }

  // 5. Try to extract Timeline
  let matchedTimeline: string | null = null;
  if (
    text.includes('immediate') ||
    text.includes('now') ||
    text.includes('ready') ||
    text.includes('asap') ||
    text.includes('1 month') ||
    text.includes('one month') ||
    text.includes('30 days') ||
    text.includes('urgent') ||
    text.includes('occupy') ||
    text.includes('next month')
  ) {
    matchedTimeline = 'Immediate';
  } else if (
    text.includes('soon') ||
    text.includes('2 month') ||
    text.includes('3 month') ||
    text.includes('1-3') ||
    text.includes('2-3') ||
    text.includes('90 days')
  ) {
    matchedTimeline = '1-3 Months';
  } else if (
    text.includes('6 month') ||
    text.includes('long') ||
    text.includes('year') ||
    text.includes('later') ||
    text.includes('exploring') ||
    text.includes('browsing') ||
    text.includes('checking')
  ) {
    matchedTimeline = '3-6 Months';
  }

  if (matchedTimeline) {
    result.timeline = matchedTimeline;
  }

  // Apply sanitization & normalization
  result.name = sanitizeName(result.name);
  result.preferred_area = normalizeAreaName(result.preferred_area);

  // Generate reply based on missing information
  const salutation = result.name ? `Thanks ${result.name}.` : 'Hi there!';

  if (!result.name) {
    result.reply = "Hello! Welcome to Apex Realty. I can help you find your dream home. May I know your name please?";
  } else if (!result.property_type && !result.preferred_area) {
    result.reply = `${salutation} Could you tell me what kind of property you are looking for (e.g., 2BHK, 3BHK flat, Villa) and in which preferred location/area?`;
  } else if (!result.property_type) {
    result.reply = `${salutation} Got it, looking in ${result.preferred_area}. What configuration are you looking for? (e.g. 2BHK flat, 3BHK apartment, or independent villa?)`;
  } else if (!result.preferred_area) {
    result.reply = `${salutation} Understood, a ${result.property_type}. Which area or location are you targeting?`;
  } else if (!result.budget) {
    result.reply = `${salutation} Excellent, a ${result.property_type} in ${result.preferred_area}. What is your comfortable budget range for this purchase?`;
  } else if (!result.timeline) {
    result.reply = `Perfect. We have several options for a ${result.property_type} in ${result.preferred_area} matching your budget of ${result.budget}. When are you planning to buy? (e.g., immediate, within 3 months, or just browsing?)`;
  } else {
    result.reply = `Awesome ${result.name}! I have noted down all your requirements:\n- Property: ${result.property_type}\n- Location: ${result.preferred_area}\n- Budget: ${result.budget}\n- Timeline: ${result.timeline}\n\nOur property consultant will contact you shortly on this number to arrange a site visit. Thank you!`;
  }

  return result;
}

// Main function to run lead qualification
export async function runAIQualification(
  history: { sender: 'lead' | 'agent' | 'salesperson'; content: string }[],
  currentData: {
    name: string | null;
    budget: string | null;
    preferred_area: string | null;
    property_type: string | null;
    timeline: string | null;
  }
): Promise<QualificationResult> {
  // If no Gemini client initialized, use the mock parser
  if (!genAI) {
    // Add small delay to feel realistic
    await new Promise(resolve => setTimeout(resolve, 800));
    console.log('[Gemini Router] No client SDK available. Fallback parser used: true');
    return runMockGeminiParser(history, currentData);
  }

  try {
    const model = genAI.getGenerativeModel({
      model: 'gemini-2.5-flash',
      systemInstruction: SYSTEM_INSTRUCTION,
      generationConfig: {
        responseMimeType: 'application/json',
        temperature: 0.2,
      },
    });

    // Format chat history for prompt context
    const historyText = history
      .map(msg => `${msg.sender.toUpperCase()}: ${msg.content}`)
      .join('\n');

    const prompt = `
Current extracted lead values (use these as starting point, update if new information was provided in the chat history):
- Name: ${currentData.name || 'null'}
- Budget: ${currentData.budget || 'null'}
- Area: ${currentData.preferred_area || 'null'}
- Property Type: ${currentData.property_type || 'null'}
- Timeline: ${currentData.timeline || 'null'}

Conversation history:
${historyText}

Analyze this conversation and return the JSON object:`;

    console.log('[Gemini Router] Gemini request sent. Prompt details:', {
      hasHistory: history.length > 0,
      currentData
    });
    
    const responseResult = await model.generateContent(prompt);
    const responseText = responseResult.response.text().trim();
    
    console.log('[Gemini Router] Gemini response received.');
    console.log('[Gemini Router] Fallback parser used: false');

    // Parse response
    const parsed: QualificationResult = JSON.parse(responseText);
    parsed.name = sanitizeName(parsed.name);
    parsed.preferred_area = normalizeAreaName(parsed.preferred_area);
    return parsed;
  } catch (error) {
    console.error('[Gemini Router] Gemini AI execution failed, falling back to local parser:', error);
    console.log('[Gemini Router] Fallback parser used: true');
    return runMockGeminiParser(history, currentData);
  }}

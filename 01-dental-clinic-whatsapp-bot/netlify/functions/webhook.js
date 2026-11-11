// ============================================
// TWILIO WEBHOOK HANDLER — Receives Patient Messages
// Receives POST requests (urlencoded) from Twilio WhatsApp Sandbox
// ============================================

const express = require('express');
const serverless = require('serverless-http');
const security = require('../../services/security');
const conversation = require('../../services/conversation');

const app = express();

// Twilio requests are sent as URL-encoded form data
app.use(express.urlencoded({ extended: false }));

// ============================================
// POST /webhook — Webhook entrypoint
// ============================================
app.post('/.netlify/functions/webhook', async (req, res) => {
  // Direct access via /webhook or redirected via redirects rule
  return handleWebhook(req, res);
});

app.post('/webhook', async (req, res) => {
  return handleWebhook(req, res);
});

async function handleWebhook(req, res) {
  const twilioSignature = req.headers['x-twilio-signature'];
  const authToken = process.env.TWILIO_AUTH_TOKEN;

  // Reconstruct the full request URL for signature validation
  // Netlify provides the absolute URL in headers, or we reconstruct it
  const protocol = req.headers['x-forwarded-proto'] || 'https';
  const host = req.headers['host'] || 'localhost';
  const fullUrl = `${protocol}://${host}${req.originalUrl || req.url}`;

  // Signature validation (strictly enforced in production, bypassed only in local development)
  // Secure-by-default: only skip when host is literally localhost (local dev server)
  const isDev = host.includes('localhost') || host.includes('127.0.0.1');
  if (!isDev) {
    const isValid = security.validateTwilioSignature(authToken, twilioSignature, fullUrl, req.body);
    if (!isValid) {
      console.warn(`[Security Alert] Invalid Twilio Signature received on webhook! Host: ${host}`);
      return res.status(403).send('Forbidden: Invalid Signature');
    }
  } else {
    console.log('[Dev mode] Skipping Twilio signature validation');
  }

  // Extract variables sent by Twilio
  // From: e.g., 'whatsapp:+919876543210'
  // Body: Message content
  const { From, Body } = req.body;

  if (!From || !Body) {
    console.warn('Received webhook request with missing From or Body parameters');
    return res.status(400).send('Bad Request: Missing From or Body');
  }

  try {
    // Hash patient phone number with SECRET_PEPPER (rainbow table protection)
    const phoneHash = security.hashPhone(From);

    // Sanitize patient text input
    const sanitizedBody = security.sanitizeInput(Body);

    console.log(`Received message from patient hash: ${phoneHash.slice(0, 8)}... (sanitized: "${sanitizedBody}")`);

    // Process message through state machine and generate response
    const replyMessage = await conversation.processMessage(phoneHash, sanitizedBody, From);

    // Respond with Twilio Markup Language (TwiML) to reply directly
    res.type('text/xml');
    res.send(`
      <Response>
        <Message>${escapeXml(replyMessage)}</Message>
      </Response>
    `.trim());

  } catch (err) {
    console.error('Error handling webhook request:', err.message);
    // Return a friendly fallback message instead of crashing
    res.type('text/xml');
    res.send(`
      <Response>
        <Message>Sorry, our booking system is experiencing a temporary issue. Please call us directly at ${process.env.CLINIC_PHONE || 'our clinic'} for assistance.</Message>
      </Response>
    `.trim());
  }
}

function escapeXml(unsafe) {
  if (typeof unsafe !== 'string') return '';
  // Strip control chars and lone surrogates which break Twilio XML
  const cleanStr = unsafe.replace(/[\x00-\x08\x0B\x0C\x0E-\x1F\uD800-\uDFFF]/g, '');
  return cleanStr.replace(/[<>&'"]/g, (c) => {
    switch (c) {
      case '<': return '&lt;';
      case '>': return '&gt;';
      case '&': return '&amp;';
      case '\'': return '&apos;';
      case '"': return '&quot;';
      default: return c;
    }
  });
}

module.exports.handler = serverless(app);

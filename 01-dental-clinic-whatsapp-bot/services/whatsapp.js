// ============================================
// WHATSAPP SERVICE — Sends messages via Twilio
// ============================================

const twilio = require('twilio');

// Lazy initialization of Twilio client
let client = null;

function getClient() {
  if (!client) {
    const accountSid = process.env.TWILIO_ACCOUNT_SID;
    const authToken = process.env.TWILIO_AUTH_TOKEN;
    if (!accountSid || !authToken) {
      throw new Error('Missing TWILIO_ACCOUNT_SID or TWILIO_AUTH_TOKEN');
    }
    client = twilio(accountSid, authToken);
  }
  return client;
}

/**
 * Send a WhatsApp message to a specific number
 * @param {string} to - The recipient's phone number (format: 'whatsapp:+91XXXXXXXXXX')
 * @param {string} body - The text content of the message
 */
async function sendMessage(to, body) {
  const fromNumber = process.env.TWILIO_WHATSAPP_NUMBER;
  if (!fromNumber) {
    throw new Error('Missing TWILIO_WHATSAPP_NUMBER environment variable');
  }

  // Ensure recipient phone format matches Twilio requirements
  let recipient = to;
  if (!recipient.startsWith('whatsapp:')) {
    recipient = `whatsapp:${recipient}`;
  }

  try {
    const message = await getClient().messages.create({
      from: fromNumber,
      to: recipient,
      body: body
    });
    console.log(`WhatsApp message sent to ${recipient}. SID: ${message.sid}`);
    return message;
  } catch (err) {
    console.error(`Failed to send WhatsApp message to ${recipient}:`, err.message);
    throw err;
  }
}

/**
 * Send an alert to the clinic admin's WhatsApp number
 * @param {string} body - The alert message text
 */
async function notifyAdmin(body) {
  const adminNumber = process.env.CLINIC_ADMIN_WHATSAPP;
  if (!adminNumber) {
    console.warn('CLINIC_ADMIN_WHATSAPP not configured — skipping admin notification');
    return null;
  }
  return sendMessage(adminNumber, body);
}

module.exports = {
  sendMessage,
  notifyAdmin
};

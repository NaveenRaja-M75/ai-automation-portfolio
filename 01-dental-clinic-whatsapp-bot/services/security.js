// ============================================
// SECURITY SERVICE — Hashing, Sanitization, Auth
// ============================================

const crypto = require('crypto');

// ============================================
// PHONE NUMBER HASHING
// ============================================

/**
 * Hash a phone number using SHA256 + SECRET_PEPPER
 * The pepper protects against rainbow-table attacks on predictable
 * Indian phone number formats (10-digit numbers with limited prefixes)
 *
 * NEVER store raw phone numbers in the database — always hash first
 */
function hashPhone(phoneNumber) {
  const pepper = process.env.SECRET_PEPPER;
  if (!pepper) {
    throw new Error('SECRET_PEPPER environment variable is not set');
  }
  return crypto
    .createHash('sha256')
    .update(phoneNumber + pepper)
    .digest('hex');
}

// ============================================
// INPUT SANITIZATION
// ============================================

/**
 * Sanitize patient input text
 * - Strips HTML tags (prevents XSS)
 * - Removes dangerous characters
 * - Trims whitespace
 * - Limits to 500 characters
 *
 * Patient text is treated as DATA only — never as instructions
 */
function sanitizeInput(text) {
  if (!text || typeof text !== 'string') return '';
  // Array.from() safely slices text without breaking emojis
  const truncated = Array.from(text).slice(0, 500).join('');
  return truncated
    .replace(/<[^>]*>/g, '')        // Strip HTML tags
    .replace(/[<>&'"\\`]/g, '')     // Remove dangerous characters
    .replace(/\r?\n/g, ' ')         // Replace newlines with spaces
    .trim();
}

// ============================================
// SESSION TOKEN GENERATION
// ============================================

/**
 * Generate a cryptographically secure random session token
 * Used for admin dashboard login sessions
 */
function generateSessionToken() {
  return crypto.randomBytes(32).toString('hex');
}

// ============================================
// TWILIO WEBHOOK VALIDATION
// ============================================

/**
 * Validate that an incoming request is genuinely from Twilio
 * Uses Twilio's request signature validation
 * This prevents attackers from sending fake webhook requests
 *
 * @param {string} authToken - Twilio Auth Token
 * @param {string} signature - X-Twilio-Signature header value
 * @param {string} url - The full webhook URL
 * @param {object} params - The request body parameters
 * @returns {boolean} true if the signature is valid
 */
function validateTwilioSignature(authToken, signature, url, params) {
  const twilio = require('twilio');

  if (!authToken) {
    throw new Error('TWILIO_AUTH_TOKEN is not set');
  }

  if (!signature) {
    return false;
  }

  return twilio.validateRequest(authToken, signature, url, params);
}

// ============================================
// EXPORTS
// ============================================

module.exports = {
  hashPhone,
  sanitizeInput,
  generateSessionToken,
  validateTwilioSignature
};

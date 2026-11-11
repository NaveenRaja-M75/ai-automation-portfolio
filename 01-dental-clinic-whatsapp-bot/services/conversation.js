// ============================================
// CONVERSATION FLOW logic — Booking State Machine
// ============================================

const db = require('./database');
const whatsapp = require('./whatsapp');

// ============================================
// CONSTANTS & CONFIGURATION
// ============================================

const STATES = {
  MENU: 'MENU',
  BOOKING_NAME: 'BOOKING_NAME',
  BOOKING_TREATMENT: 'BOOKING_TREATMENT',
  BOOKING_DATE_CHOICE: 'BOOKING_DATE_CHOICE', // Choosing today/tomorrow/day after/specific date
  BOOKING_DATE_CUSTOM: 'BOOKING_DATE_CUSTOM', // Waiting for specific typed date
  BOOKING_TIME: 'BOOKING_TIME',
  BOOKING_CONFIRM: 'BOOKING_CONFIRM',
  CANCEL_CONFIRM: 'CANCEL_CONFIRM',
  AWAITING_REMINDER: 'AWAITING_REMINDER'
};

const TREATMENTS = [
  'General checkup & cleaning',
  'Tooth filling',
  'Root canal treatment',
  'Tooth extraction',
  'Braces / alignment',
  'Teeth whitening'
];

const VALID_TIMES = [
  '9:00 AM', '10:00 AM', '11:00 AM',
  '2:00 PM', '3:00 PM', '4:00 PM', '5:00 PM',
  '6:00 PM', '7:00 PM'
];

// ============================================
// MAIN STATE MACHINE ENTRYPOINT
// ============================================

/**
 * Process incoming patient WhatsApp message
 * @param {string} phoneHash - SHA256 hashed sender phone number
 * @param {string} messageText - Sanitized text content of message
 * @param {string} rawPhoneNumber - Raw phone number (needed for Twilio outbound reply/alert)
 */
async function processMessage(phoneHash, messageText, rawPhoneNumber) {
  // 1. Get or create conversation state
  let session = await db.getConversation(phoneHash);
  const normalizedText = messageText.trim().toLowerCase();

  // If new session, initialize to MENU state
  if (!session) {
    session = await db.upsertConversation(phoneHash, STATES.MENU, {});
    return getMenuMessage();
  }

  const state = session.current_step;
  const tempData = session.temp_data || {};

  // Bug 1: Reset conversation to MENU if patient says a greeting
  const greetings = ['hi', 'hello', 'hey', 'start', 'menu', 'help'];
  if (greetings.includes(normalizedText)) {
    await db.upsertConversation(phoneHash, STATES.MENU, {});
    return getMenuMessage();
  }

  // Check if patient is replying to a reminder with CONFIRM
  if (normalizedText === 'confirm') {
    return await handleReminderReply(phoneHash, 'confirm');
  }

  // Bug 3: Handle cancel globally
  if (normalizedText === 'cancel' || normalizedText === 'cancel appointment') {
    return await initiateCancel(phoneHash);
  }

  // 2. Route message to state-specific handler with safety guards
  let reply;
  try {
    switch (state) {
      case STATES.MENU:
        reply = await handleMenuState(phoneHash, normalizedText, tempData);
        break;
      case STATES.BOOKING_NAME:
        reply = await handleBookingName(phoneHash, messageText, tempData);
        break;
      case STATES.BOOKING_TREATMENT:
        reply = await handleBookingTreatment(phoneHash, normalizedText, messageText, tempData);
        break;
      case STATES.BOOKING_DATE_CHOICE:
        reply = await handleBookingDateChoice(phoneHash, normalizedText, messageText, tempData);
        break;
      case STATES.BOOKING_DATE_CUSTOM:
        reply = await handleBookingDateCustom(phoneHash, normalizedText, messageText, tempData);
        break;
      case STATES.BOOKING_TIME:
        reply = await handleBookingTime(phoneHash, normalizedText, messageText, tempData, rawPhoneNumber);
        break;
      case STATES.BOOKING_CONFIRM:
        reply = await handleBookingConfirm(phoneHash, normalizedText, tempData, rawPhoneNumber);
        break;
      case STATES.CANCEL_CONFIRM:
        reply = await handleCancelConfirm(phoneHash, normalizedText, tempData);
        break;
      default:
        // Reset state if invalid/corrupt
        await db.upsertConversation(phoneHash, STATES.MENU, {});
        reply = getMenuMessage();
    }
  } catch (err) {
    console.error('Conversation handler error:', err);
    // Preserve current state for retry but give a friendly fallback
    reply = 'Sorry, something went wrong. Please type *1* to return to the main menu.';
  }
  return reply;
}

// ============================================
// STATE HANDLERS
// ============================================

/**
 * Main Menu State Handler
 */
async function handleMenuState(phoneHash, normalizedText, tempData) {
  switch (normalizedText) {
    case '1':
    case 'one':
    case 'book':
    case 'book an appointment':
      await db.upsertConversation(phoneHash, STATES.BOOKING_NAME, {});
      return 'May I have your full name please?';

    case '2':
    case 'two':
    case 'hours':
    case 'location':
      return getHoursAndLocation();

    case '3':
    case 'three':
    case 'treatments':
    case 'prices':
      return getTreatmentsAndPrices();

    case '4':
    case 'four':
    case 'emergency':
      return getEmergencyMessage();

    case '5':
    case 'five':
    case 'staff':
    case 'talk to staff':
      // Send alert to clinic admin WhatsApp
      try {
        const last4 = phoneHash.slice(-4);
        await whatsapp.notifyAdmin(`Patient [phone_hash_last4: ${last4}] wants to speak to staff.`);
      } catch (adminAlertErr) {
        console.warn('[Admin Notification Warning] Failed to alert admin for talk-to-staff request:', adminAlertErr.message);
      }
      return getTalkToStaffMessage();

    default:
      return getMenuMessage(true); // Show fallback prompt
  }
}

/**
 * Step A: Name Handler
 */
async function handleBookingName(phoneHash, messageText, tempData) {
  const name = messageText.trim();

  // Basic name validation: at least 2 chars, letters and spaces only
  if (name.length < 2 || !/^[a-zA-Z\s.]+$/.test(name)) {
    return 'Please enter a valid name (letters and spaces only, minimum 2 characters).';
  }

  tempData.patient_name = name;
  await db.upsertConversation(phoneHash, STATES.BOOKING_TREATMENT, tempData);

  return `Thanks, ${name}! What treatment are you looking for?\n\n` +
    `1️⃣ General checkup & cleaning\n` +
    `2️⃣ Tooth filling\n` +
    `3️⃣ Root canal treatment\n` +
    `4️⃣ Tooth extraction\n` +
    `5️⃣ Braces / alignment\n` +
    `6️⃣ Teeth whitening\n` +
    `7️⃣ Other (type your concern)`;
}

/**
 * Step B: Treatment Handler
 */
async function handleBookingTreatment(phoneHash, normalizedText, rawText, tempData) {
  let treatment = '';

  // Handle number choices
  if (normalizedText === '1') treatment = TREATMENTS[0];
  else if (normalizedText === '2') treatment = TREATMENTS[1];
  else if (normalizedText === '3') treatment = TREATMENTS[2];
  else if (normalizedText === '4') treatment = TREATMENTS[3];
  else if (normalizedText === '5') treatment = TREATMENTS[4];
  else if (normalizedText === '6') treatment = TREATMENTS[5];
  else if (normalizedText === '7' || normalizedText.includes('other')) {
    treatment = 'Other';
  } else {
    // Check if they typed a matches text prefix
    const match = TREATMENTS.find(t => t.toLowerCase().includes(normalizedText));
    if (match) {
      treatment = match;
    }
  }

  // If they typed something custom
  if (!treatment) {
    // If not matching a menu choice, check if they typed a generic custom concern
    treatment = rawText.trim();
  }

  tempData.treatment_type = treatment;
  await db.upsertConversation(phoneHash, STATES.BOOKING_DATE_CHOICE, tempData);

  return `Which date works for you?\n\n` +
    `1️⃣ Today\n` +
    `2️⃣ Tomorrow\n` +
    `3️⃣ Day after tomorrow\n` +
    `4️⃣ Type a specific date (e.g., 18 July or 18/07)`;
}

/**
 * Step C: Date Choice Handler
 */
async function handleBookingDateChoice(phoneHash, normalizedText, rawText, tempData) {
  let dateStr = '';
  let displayStr = '';

  if (normalizedText === '1' || normalizedText.includes('today')) {
    dateStr = db.getTodayIST();
    displayStr = 'Today';
  } else if (normalizedText === '2' || normalizedText.includes('tomorrow')) {
    dateStr = db.getDateOffsetIST(1);
    displayStr = 'Tomorrow';
  } else if (normalizedText === '3' || normalizedText.includes('day after')) {
    dateStr = db.getDateOffsetIST(2);
    displayStr = 'Day after tomorrow';
  } else if (normalizedText === '4' || normalizedText.includes('specific') || normalizedText.includes('type')) {
    await db.upsertConversation(phoneHash, STATES.BOOKING_DATE_CUSTOM, tempData);
    return 'Please type your preferred date (e.g. 18 July or 18/07):';
  } else {
    // Check if they typed a date directly in this step
    const parsed = parseDateString(rawText);
    if (parsed) {
      dateStr = parsed.dateStr;
      displayStr = parsed.displayStr;
    } else {
      return 'Please choose one of the options (1, 2, 3, or 4):';
    }
  }

  tempData.appt_date = dateStr;
  tempData.appt_date_display = displayStr;
  await db.upsertConversation(phoneHash, STATES.BOOKING_TIME, tempData);

  return getPreferredTimePrompt(displayStr);
}

/**
 * Custom Date Input Handler (DD Month or DD/MM)
 */
async function handleBookingDateCustom(phoneHash, normalizedText, rawText, tempData) {
  const parsed = parseDateString(rawText);
  if (!parsed) {
    return 'I couldn\'t parse that date 😅\n\n' +
      'Please type it clearly using formats like:\n' +
      '• *18 July* (day and month name)\n' +
      '• *18/07* (DD/MM format)\n' +
      '• *18-07* (DD-MM format)';
  }

  // Validate past dates (compared to today in IST)
  const today = db.getTodayIST();
  if (parsed.dateStr < today) {
    return `That date is in the past! Today is ${formatDisplayDate(today)}. Please enter a future date:`;
  }

  tempData.appt_date = parsed.dateStr;
  tempData.appt_date_display = parsed.displayStr;
  await db.upsertConversation(phoneHash, STATES.BOOKING_TIME, tempData);

  return getPreferredTimePrompt(parsed.displayStr);
}

/**
 * Step D: Preferred Time Handler (Includes double-booking checks)
 */
async function handleBookingTime(phoneHash, normalizedText, rawText, tempData, rawPhoneNumber) {
  const normalizedTime = normalizeTimeInput(rawText);

  if (!normalizedTime) {
    return 'Please select a valid clinic time:\n' +
      '🌅 Morning: 9 AM, 10 AM, 11 AM\n' +
      '☀️ Afternoon: 2 PM, 3 PM, 4 PM, 5 PM\n' +
      '🌆 Evening: 6 PM, 7 PM\n\n' +
      'Simply type the time you prefer (e.g. 10 AM or 4 PM).';
  }

  const selectedDate = tempData.appt_date;

  // Perform Double-booking check
  const available = await db.checkSlotAvailable(selectedDate, normalizedTime);

  if (!available) {
    // Slot taken -> suggest next 2 available slots on same day
    const suggestions = await db.getNextAvailableSlots(selectedDate, normalizedTime, 2);
    if (suggestions.length > 0) {
      return `Sorry, ${normalizedTime} on ${tempData.appt_date_display} is already booked. 😞\n\n` +
        `The following slots are available on that day:\n` +
        suggestions.map(s => `• *${s}*`).join('\n') + `\n\n` +
        `Please type one of these times or type a different slot.`;
    } else {
      return `Sorry, ${normalizedTime} is booked, and there are no other slots left on ${tempData.appt_date_display}.\n\n` +
        `Please type "1" to choose another date or choose a different time.`;
    }
  }

  // Slot is available! Record time and ask for confirmation
  tempData.appt_time = normalizedTime;
  await db.upsertConversation(phoneHash, STATES.BOOKING_CONFIRM, tempData);

  return `Please confirm your appointment:\n` +
    `👤 *Name:* ${tempData.patient_name}\n` +
    `🦷 *Treatment:* ${tempData.treatment_type}\n` +
    `📅 *Date:* ${tempData.appt_date_display} (${formatDisplayDate(tempData.appt_date)})\n` +
    `⏰ *Time:* ${normalizedTime}\n\n` +
    `Reply *YES* to confirm or *NO* to change anything.`;
}

/**
 * Step E: Confirmation Handler
 */
async function handleBookingConfirm(phoneHash, normalizedText, tempData, rawPhoneNumber) {
  if (normalizedText === 'yes' || normalizedText === 'confirm' || normalizedText === 'y') {
    try {
      // 1. Save appointment to Supabase
      const appt = await db.createAppointment({
        patient_name: tempData.patient_name,
        phone_hash: phoneHash,
        patient_phone: rawPhoneNumber,
        treatment_type: tempData.treatment_type,
        appt_date: tempData.appt_date,
        appt_time: tempData.appt_time
      });

      const clinicName = process.env.CLINIC_NAME || 'Demo Dental Clinic';
      const clinicAddress = process.env.CLINIC_ADDRESS || '123 Anna Nagar, Chennai';

      // 2. Alert clinic admin (wrapped in try-catch so it doesn't crash the flow if number is invalid)
      try {
        await whatsapp.notifyAdmin(
          `🆕 *New Appointment Booked!*\n` +
          `👤 Name: ${tempData.patient_name}\n` +
          `🦷 Treatment: ${tempData.treatment_type}\n` +
          `📅 Date: ${tempData.appt_date} (${tempData.appt_date_display})\n` +
          `⏰ Time: ${tempData.appt_time}\n` +
          `📞 Phone Hash: ${phoneHash.slice(0, 10)}...`
        );
      } catch (adminNotifyErr) {
        console.warn('[Admin Notification Warning] Failed to send WhatsApp alert to admin:', adminNotifyErr.message);
      }

      // 3. Clear conversation state
      await db.upsertConversation(phoneHash, STATES.MENU, {});

      // 4. Return confirmation reply
      return `✅ Appointment confirmed!\n` +
        `We'll send you a reminder the morning before.\n\n` +
        `See you soon at:\n📍 ${clinicAddress}\n\n` +
        `Questions? Just reply here anytime.`;
    } catch (err) {
      console.error('Error confirming appointment:', err);
      // Keep the user in the confirm step to retry
      return 'Sorry, there was a problem saving your appointment. Please reply *YES* to try again or *NO* to start over.';
    }

  } else if (normalizedText === 'no' || normalizedText === 'change' || normalizedText === 'n') {
    // Go back to the menu to restart or pick a slot
    await db.upsertConversation(phoneHash, STATES.MENU, {});
    return `No problem! Let's start over.\n\n` + getMenuMessage();
  } else {
    return 'Please reply with *YES* to confirm your appointment, or *NO* to start over.';
  }
}

/**
 * Handler for globally caught reminder replies (CONFIRM/CANCEL)
 */
async function handleReminderReply(phoneHash, command) {
  // Locate tomorrow's appointment for this patient
   const tomorrow = db.getDateOffsetIST(1);
   const appointments = await db.getAllAppointments();

   // Find latest active appointment tomorrow matching this patient's hash
   const activeAppt = appointments.find(
     a => a.phone_hash === phoneHash && a.appt_date === tomorrow && ['pending', 'confirmed'].includes(a.status)
   );

   if (!activeAppt) {
     // If no upcoming appointment tomorrow, treat as normal message
     return `I couldn't find an appointment for you tomorrow. 😊\n\n` + getMenuMessage();
   }

   if (command === 'confirm') {
     await db.updateAppointmentStatus(activeAppt.id, 'confirmed');
     // Reset conversation to menu after handling reminder reply
     await db.upsertConversation(phoneHash, STATES.MENU, {});
     return 'Thank you for confirming! ✅ See you tomorrow.';
   } else if (command === 'cancel') {
     await db.updateAppointmentStatus(activeAppt.id, 'cancelled');
     await db.upsertConversation(phoneHash, STATES.MENU, {});
     return 'No problem! We\'ve noted your cancellation. ❌\n\n' +
       'Type *1* to book a new appointment anytime.';
   }

   return getMenuMessage();
}

// ============================================
// HELPER METHODS & UTILITIES
// ============================================

function getMenuMessage(isError = false) {
  const greeting = isError
    ? 'I didn\'t quite get that 😊 Please choose from our menu:'
    : 'Hello! 👋 Welcome to Demo Dental Clinic. I\'m your dental assistant.\n\nHow can I help you today?';

  return `${greeting}\n\n` +
    `1️⃣ Book an appointment\n` +
    `2️⃣ Clinic hours & location\n` +
    `3️⃣ Treatments & rough prices\n` +
    `4️⃣ Emergency / urgent help\n` +
    `5️⃣ Talk to our staff`;
}

function getHoursAndLocation() {
  const clinicName = process.env.CLINIC_NAME || 'Demo Dental Clinic';
  const clinicAddress = process.env.CLINIC_ADDRESS || '123 Anna Nagar, Chennai';
  const clinicPhone = process.env.CLINIC_PHONE || '+91XXXXXXXXXX';

  return `📅 *Clinic Hours:*\n` +
    `• Monday – Saturday: 9 AM – 7 PM\n` +
    `• Sunday: Closed\n\n` +
    `📍 *Location:*\n${clinicAddress}\n\n` +
    `📞 *Calls:* ${clinicPhone}\n\n` +
    `Type *1* to book an appointment.`;
}

function getTreatmentsAndPrices() {
  return `🦷 *Common treatments & prices:*\n` +
    `• Checkup & cleaning: ₹300 – ₹500\n` +
    `• Tooth filling: ₹500 – ₹1,500\n` +
    `• Root canal: ₹3,000 – ₹8,000\n` +
    `• Extraction: ₹500 – ₹2,000\n` +
    `• Braces: ₹20,000+\n` +
    `• Whitening: ₹3,000 – ₹6,000\n\n` +
    `_Prices are approximate. Final cost depends on examination._\n\n` +
    `Type *1* to book an appointment.`;
}

function getEmergencyMessage() {
  const clinicAddress = process.env.CLINIC_ADDRESS || '123 Anna Nagar, Chennai';
  const clinicPhone = process.env.CLINIC_PHONE || '+91XXXXXXXXXX';

  return `🚨 *Dental Emergencies:*\n` +
    `Please call us immediately:\n` +
    `📞 ${clinicPhone}\n\n` +
    `Or visit us at:\n` +
    `📍 ${clinicAddress}\n\n` +
    `We keep emergency slots available daily.\n` +
    `_If outside clinic hours, please proceed to the nearest 24-hour hospital._`;
}

function getTalkToStaffMessage() {
  const clinicPhone = process.env.CLINIC_PHONE || '+91XXXXXXXXXX';

  return `Connecting you to our staff! 🙂\n\n` +
    `They'll reply here during clinic hours (9 AM – 7 PM, Mon–Sat).\n\n` +
    `You can also call us directly:\n` +
    `📞 ${clinicPhone}`;
}

function getPreferredTimePrompt(displayDate) {
  return `Preferred time for ${displayDate}?\n\n` +
    `🌅 *Morning:* 9 AM · 10 AM · 11 AM\n` +
    `☀️ *Afternoon:* 2 PM · 3 PM · 4 PM · 5 PM\n` +
    `🌆 *Evening:* 6 PM · 7 PM\n\n` +
    `Type the time you prefer (e.g. 10 AM or 4 PM).`;
}

function formatDisplayDate(dateStr) {
  // YYYY-MM-DD to DD Month Name YYYY
  const date = new Date(dateStr + 'T00:00:00');
  return date.toLocaleDateString('en-IN', { day: 'numeric', month: 'short', year: 'numeric' });
}

/**
 * Normalizes time inputs to match VALID_TIMES values exactly
 * Matches numbers, prefixes (AM/PM), spaces, lowercase
 * e.g. "9", "9am", "9:00", "9:00am", "2 pm" -> "9:00 AM", "2:00 PM"
 */
function normalizeTimeInput(timeText) {
  const fillerWords = /\b(morning|afternoon|evening|around|at|about|approx|approximately)\b/gi;
  const cleaned = timeText
    .trim()
    .replace(fillerWords, '')   // "Morning 10 am" → " 10 am"
    .replace(/[?.,!]+/g, '')    // "10am?" → "10am"
    .replace(/\s+/g, '')        // " 10 am" → "10am"
    .toLowerCase();

  const mapping = {
    '9': '9:00 AM', '9am': '9:00 AM', '9:00': '9:00 AM', '9:00am': '9:00 AM',
    '10': '10:00 AM', '10am': '10:00 AM', '10:00': '10:00 AM', '10:00am': '10:00 AM',
    '11': '11:00 AM', '11am': '11:00 AM', '11:00': '11:00 AM', '11:00am': '11:00 AM',
    '2': '2:00 PM', '2pm': '2:00 PM', '2:00': '2:00 PM', '2:00pm': '2:00 PM',
    '3': '3:00 PM', '3pm': '3:00 PM', '3:00': '3:00 PM', '3:00pm': '3:00 PM',
    '4': '4:00 PM', '4pm': '4:00 PM', '4:00': '4:00 PM', '4:00pm': '4:00 PM',
    '5': '5:00 PM', '5pm': '5:00 PM', '5:00': '5:00 PM', '5:00pm': '5:00 PM',
    '6': '6:00 PM', '6pm': '6:00 PM', '6:00': '6:00 PM', '6:00pm': '6:00 PM',
    '7': '7:00 PM', '7pm': '7:00 PM', '7:00': '7:00 PM', '7:00pm': '7:00 PM'
  };

  return mapping[cleaned] || null;
}

/**
 * Custom parser for user date inputs (DD Month or DD/MM or DD-MM)
 * Returns { dateStr: 'YYYY-MM-DD', displayStr: '18 July' }
 */
function parseDateString(text) {
  const cleaned = text.trim().toLowerCase();
  const today = new Date();

  // Try parsing DD/MM or DD-MM format
  const slashMatch = cleaned.match(/^(\d{1,2})[-/](\d{1,2})$/);
  if (slashMatch) {
    const day = parseInt(slashMatch[1]);
    const month = parseInt(slashMatch[2]) - 1; // JS months are 0-indexed
    if (day >= 1 && day <= 31 && month >= 0 && month <= 11) {
      let year = today.getFullYear();
      // If date is in the past, it might refer to next year (e.g., Dec to Jan transition)
      const targetDate = new Date(year, month, day);
      if (targetDate < today && (today.getTime() - targetDate.getTime() > 10 * 24 * 60 * 60 * 1000)) {
        year += 1;
      }
      const date = new Date(year, month, day);
      return {
        dateStr: date.toLocaleDateString('en-CA', { timeZone: 'Asia/Kolkata' }),
        displayStr: date.toLocaleDateString('en-IN', { day: 'numeric', month: 'long' })
      };
    }
  }

  // Try parsing "DD Month" format (e.g. "18 July", "18 Jul", "3rd July")
  // Strip ordinal indicators (st, nd, rd, th)
  const textCleaned = cleaned.replace(/\b(\d+)(st|nd|rd|th)\b/g, '$1');
  const monthMatch = textCleaned.match(/^(\d{1,2})\s+([a-z]+)$/);
  if (monthMatch) {
    const day = parseInt(monthMatch[1]);
    const monthStr = monthMatch[2];

    const months = [
      ['jan', 'january'], ['feb', 'february'], ['mar', 'march'],
      ['apr', 'april'], ['may'], ['jun', 'june'],
      ['jul', 'july'], ['aug', 'august'], ['sep', 'september', 'sept'],
      ['oct', 'october'], ['nov', 'november'], ['dec', 'december']
    ];

    const monthIndex = months.findIndex(m => m.some(name => monthStr.startsWith(name)));
    if (day >= 1 && day <= 31 && monthIndex !== -1) {
      let year = today.getFullYear();
      const targetDate = new Date(year, monthIndex, day);
      // If it refers to last year, shift to next year
      if (targetDate < today && (today.getTime() - targetDate.getTime() > 10 * 24 * 60 * 60 * 1000)) {
        year += 1;
      }
      const date = new Date(year, monthIndex, day);
      return {
        dateStr: date.toLocaleDateString('en-CA', { timeZone: 'Asia/Kolkata' }),
        displayStr: date.toLocaleDateString('en-IN', { day: 'numeric', month: 'long' })
      };
    }
  }

  return null;
}

/**
 * Initiates the cancellation flow by finding the nearest active appointment
 */
async function initiateCancel(phoneHash) {
  const appointments = await db.getAllAppointments();
  const today = db.getTodayIST();
  
  // Find active appointments from today onwards, sorted by closest date first
  const activeAppts = appointments
    .filter(a => a.phone_hash === phoneHash && ['pending', 'confirmed'].includes(a.status) && a.appt_date >= today)
    .sort((a, b) => new Date(a.appt_date) - new Date(b.appt_date));

  if (activeAppts.length === 0) {
    return `I couldn't find any upcoming appointments for you to cancel. 😊\n\n` + getMenuMessage();
  }

  const targetAppt = activeAppts[0];

  // Set state to CANCEL_CONFIRM and save appt details to temp data
  await db.upsertConversation(phoneHash, STATES.CANCEL_CONFIRM, { 
    cancel_appt_id: targetAppt.id, 
    cancel_date: targetAppt.appt_date, 
    cancel_time: targetAppt.appt_time 
  });

  return `Are you sure you want to cancel your appointment on *${targetAppt.appt_date}* at *${targetAppt.appt_time}*?\n\n` +
         `Reply *YES* to cancel, or *NO* to keep it.`;
}

/**
 * Handles the YES/NO response for cancelling an appointment
 */
async function handleCancelConfirm(phoneHash, normalizedText, tempData) {
  if (normalizedText === 'yes' || normalizedText === 'y') {
    if (tempData.cancel_appt_id) {
      await db.updateAppointmentStatus(tempData.cancel_appt_id, 'cancelled');
    }
    await db.upsertConversation(phoneHash, STATES.MENU, {});
    return 'Your appointment has been successfully cancelled. ❌\n\n' +
           'Type *1* anytime if you want to book a new appointment.';
  } else if (normalizedText === 'no' || normalizedText === 'n') {
    await db.upsertConversation(phoneHash, STATES.MENU, {});
    return 'Great! We have kept your appointment. ✅\n\n' + getMenuMessage();
  } else {
    return `Please reply with *YES* to cancel your appointment on ${tempData.cancel_date}, or *NO* to keep it.`;
  }
}

// ============================================
// EXPORTS
// ============================================

module.exports = {
  processMessage,
  STATES
};

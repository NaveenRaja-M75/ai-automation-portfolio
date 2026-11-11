// ============================================
// REMINDER SERVICE — Netlify Scheduled Function
// Runs daily at 9:00 AM IST (30 3 * * * UTC)
// ============================================

const db = require('../../services/database');
const whatsapp = require('../../services/whatsapp');

// ============================================
// SCHEDULED CONFIGURATION
// ============================================
// We export the configuration for Netlify to schedule this function
export const config = {
  schedule: process.env.REMINDER_CRON || "30 3 * * *"
};

// ============================================
// SCHEDULED FUNCTION HANDLER
// ============================================
export const handler = async (event, context) => {
  console.log('Daily scheduled reminder task started...');
  const results = {
    remindersSentCount: 0,
    failuresCount: 0,
    sessionsCleaned: false,
    errors: []
  };

  try {
    // 1. Fetch upcoming appointments for tomorrow needing reminders
    const upcoming = await db.getUpcomingReminders();
    console.log(`Found ${upcoming.length} appointments for tomorrow requiring reminders.`);

    const clinicName = process.env.CLINIC_NAME || 'Demo Dental Clinic';
    const clinicAddress = process.env.CLINIC_ADDRESS || '123 Anna Nagar, Chennai';

    // 2. Iterate and send reminders to each patient
    for (const appt of upcoming) {
      if (!appt.patient_phone) {
        console.warn(`[Reminder Warning] Appointment ID ${appt.id} is missing patient_phone — skipping.`);
        results.failuresCount++;
        results.errors.push(`Appt ID ${appt.id}: Missing phone number`);
        continue;
      }

      const messageText = 
        `Hi ${appt.patient_name}! 🦷 Reminder: You have a dental appointment ` +
        `at ${clinicName} tomorrow at ${appt.appt_time}.\n\n` +
        `Reply *CONFIRM* to confirm ✅\n` +
        `Reply *CANCEL* to reschedule ❌\n\n` +
        `📍 Location: ${clinicAddress}\n` +
        `See you tomorrow!`;

      try {
        console.log(`Sending reminder to Appt ID ${appt.id} (${appt.patient_name})...`);
        
        // Send WhatsApp message
        await whatsapp.sendMessage(appt.patient_phone, messageText);
        
        // Mark as reminder_sent in database
        await db.markReminderSent(appt.id);
        
        // Put conversation state into AWAITING_REMINDER so the bot catches their CONFIRM/CANCEL reply
        await db.upsertConversation(appt.phone_hash, 'AWAITING_REMINDER', {
          patient_name: appt.patient_name,
          treatment_type: appt.treatment_type,
          appt_date: appt.appt_date,
          appt_time: appt.appt_time
        });

        results.remindersSentCount++;
      } catch (sendErr) {
        console.error(`[Reminder Error] Failed to send to ${appt.patient_name} (Appt ID ${appt.id}):`, sendErr.message);
        results.failuresCount++;
        results.errors.push(`Appt ID ${appt.id}: ${sendErr.message}`);
      }
    }

    // 3. Clean up expired admin dashboard sessions (older than 24 hours)
    console.log('Cleaning up expired admin sessions...');
    await db.cleanupExpiredSessions();
    results.sessionsCleaned = true;

    console.log('Daily scheduled reminder task completed successfully.', results);
    return {
      statusCode: 200,
      body: JSON.stringify({ message: "Scheduler completed successfully", results })
    };

  } catch (err) {
    console.error('Error in scheduled reminder task:', err.message);
    return {
      statusCode: 500,
      body: JSON.stringify({ error: err.message })
    };
  }
};

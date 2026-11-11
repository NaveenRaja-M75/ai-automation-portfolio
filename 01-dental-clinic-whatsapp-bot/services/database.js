// ============================================
// DATABASE SERVICE — All Supabase Operations
// ============================================

const { createClient } = require('@supabase/supabase-js');

// ============================================
// SUPABASE CLIENT (lazy initialization)
// ============================================

let supabase = null;

function getClient() {
  if (!supabase) {
    const url = process.env.SUPABASE_URL;
    const key = process.env.SUPABASE_ANON_KEY;
    if (!url || !key) {
      throw new Error('Missing SUPABASE_URL or SUPABASE_ANON_KEY environment variables');
    }
    supabase = createClient(url, key);
  }
  return supabase;
}

// ============================================
// DATE UTILITIES (IST — India Standard Time)
// ============================================

// All time slots offered by the clinic
const ALL_TIME_SLOTS = [
  '9:00 AM', '10:00 AM', '11:00 AM',
  '2:00 PM', '3:00 PM', '4:00 PM', '5:00 PM',
  '6:00 PM', '7:00 PM'
];

/**
 * Get today's date in IST as YYYY-MM-DD
 * Uses Asia/Kolkata timezone so it's correct regardless of server location
 */
function getTodayIST() {
  return new Date().toLocaleDateString('en-CA', { timeZone: 'Asia/Kolkata' });
}

/**
 * Get a date N days from today in IST as YYYY-MM-DD
 */
function getDateOffsetIST(days) {
  const d = new Date();
  d.setDate(d.getDate() + days);
  return d.toLocaleDateString('en-CA', { timeZone: 'Asia/Kolkata' });
}

// ============================================
// APPOINTMENTS — CRUD Operations
// ============================================

/**
 * Create a new appointment
 */
async function createAppointment({ patient_name, phone_hash, patient_phone, treatment_type, appt_date, appt_time }) {
  // Guard against duplicate bookings (phone_hash + date + time)
  const { data: existing, error: existingErr } = await getClient()
    .from('appointments')
    .select('id')
    .eq('phone_hash', phone_hash)
    .eq('appt_date', appt_date)
    .eq('appt_time', appt_time)
    .in('status', ['pending', 'confirmed'])
    .single();

  if (existingErr && existingErr.code !== 'PGRST116') {
    // Real query error
    throw existingErr;
  }
  if (existing) {
    // Return the existing appointment record (no new insert)
    return existing;
  }

  const { data, error } = await getClient()
    .from('appointments')
    .insert({
      patient_name,
      phone_hash,
      patient_phone,
      treatment_type,
      appt_date,
      appt_time,
      status: 'confirmed'
    })
    .select()
    .single();

  if (error) throw error;
  return data;
}

/**
 * Get all appointments for a specific date, sorted by time
 */
async function getAppointmentsByDate(date) {
  const { data, error } = await getClient()
    .from('appointments')
    .select('*')
    .eq('appt_date', date)
    .order('appt_time', { ascending: true });

  if (error) throw error;
  return data || [];
}

/**
 * Get appointments within a date range (inclusive), sorted by date then time
 */
async function getAppointmentsRange(startDate, endDate) {
  const { data, error } = await getClient()
    .from('appointments')
    .select('*')
    .gte('appt_date', startDate)
    .lte('appt_date', endDate)
    .order('appt_date', { ascending: true })
    .order('appt_time', { ascending: true });

  if (error) throw error;
  return data || [];
}

/**
 * Get all appointments (most recent first), capped at 500
 */
async function getAllAppointments() {
  const { data, error } = await getClient()
    .from('appointments')
    .select('*')
    .order('appt_date', { ascending: false })
    .order('appt_time', { ascending: true })
    .limit(500);

  if (error) throw error;
  return data || [];
}

/**
 * Update an appointment's status
 */
async function updateAppointmentStatus(id, status) {
  const validStatuses = ['pending', 'confirmed', 'completed', 'cancelled', 'no-show'];
  if (!validStatuses.includes(status)) {
    throw new Error(`Invalid status: ${status}. Must be one of: ${validStatuses.join(', ')}`);
  }

  const { data, error } = await getClient()
    .from('appointments')
    .update({ status })
    .eq('id', id)
    .select()
    .single();

  if (error) throw error;
  return data;
}

// ============================================
// APPOINTMENTS — Slot Availability
// ============================================

/**
 * Check if a specific date/time slot is available
 * A slot is taken if there's an active appointment (pending or confirmed)
 */
async function checkSlotAvailable(date, time) {
  const { data, error } = await getClient()
    .from('appointments')
    .select('id')
    .eq('appt_date', date)
    .eq('appt_time', time)
    .in('status', ['pending', 'confirmed']);

  if (error) throw error;
  return !data || data.length === 0;
}

/**
 * Find the next N available time slots on a given date
 * Starts looking after the requested time, then wraps around
 */
async function getNextAvailableSlots(date, requestedTime, count = 2) {
  // Get all booked times for this date
  const { data: booked, error } = await getClient()
    .from('appointments')
    .select('appt_time')
    .eq('appt_date', date)
    .in('status', ['pending', 'confirmed']);

  if (error) throw error;

  const bookedTimes = new Set((booked || []).map(a => a.appt_time));
  const requestedIndex = ALL_TIME_SLOTS.indexOf(requestedTime);
  const available = [];

  // First, check slots AFTER the requested time
  for (let i = requestedIndex + 1; i < ALL_TIME_SLOTS.length && available.length < count; i++) {
    if (!bookedTimes.has(ALL_TIME_SLOTS[i])) {
      available.push(ALL_TIME_SLOTS[i]);
    }
  }

  // If still need more, check slots BEFORE the requested time
  if (available.length < count) {
    for (let i = 0; i < requestedIndex && available.length < count; i++) {
      if (!bookedTimes.has(ALL_TIME_SLOTS[i])) {
        available.push(ALL_TIME_SLOTS[i]);
      }
    }
  }

  return available;
}

// ============================================
// APPOINTMENTS — Reminders
// ============================================

/**
 * Get appointments for tomorrow that haven't had reminders sent yet
 * Only includes pending or confirmed appointments
 */
async function getUpcomingReminders() {
  const tomorrow = getDateOffsetIST(1);

  const { data, error } = await getClient()
    .from('appointments')
    .select('*')
    .eq('appt_date', tomorrow)
    .eq('reminder_sent', false)
    .in('status', ['pending', 'confirmed']);

  if (error) throw error;
  return data || [];
}

/**
 * Mark a reminder as sent for an appointment
 */
async function markReminderSent(id) {
  const { error } = await getClient()
    .from('appointments')
    .update({ reminder_sent: true })
    .eq('id', id);

  if (error) throw error;
}

// ============================================
// MONTHLY STATS
// ============================================

/**
 * Get booking stats for the current month
 * Returns { totalBookings, noShows }
 */
async function getMonthlyStats() {
  const now = new Date();
  const firstOfMonth = new Date(now.getFullYear(), now.getMonth(), 1)
    .toLocaleDateString('en-CA', { timeZone: 'Asia/Kolkata' });
  const lastOfMonth = new Date(now.getFullYear(), now.getMonth() + 1, 0)
    .toLocaleDateString('en-CA', { timeZone: 'Asia/Kolkata' });

  const { data, error } = await getClient()
    .from('appointments')
    .select('status')
    .gte('appt_date', firstOfMonth)
    .lte('appt_date', lastOfMonth);

  if (error) throw error;

  const all = data || [];
  return {
    totalBookings: all.length,
    noShows: all.filter(a => a.status === 'no-show').length
  };
}

/**
 * Get count of today's appointments
 */
async function getTodayCount() {
  const today = getTodayIST();
  const { data, error } = await getClient()
    .from('appointments')
    .select('id')
    .eq('appt_date', today);

  if (error) throw error;
  return (data || []).length;
}

// ============================================
// CONVERSATIONS — Booking Flow State
// ============================================

/**
 * Get the current conversation state for a patient (by phone hash)
 * Returns null if no conversation exists
 */
async function getConversation(phoneHash) {
  const { data, error } = await getClient()
    .from('conversations')
    .select('*')
    .eq('phone_hash', phoneHash)
    .single();

  // PGRST116 = "not found" — expected for new patients
  if (error && error.code !== 'PGRST116') throw error;
  return data || null;
}

/**
 * Create or update a conversation state
 * Uses upsert to handle both new and returning patients
 */
async function upsertConversation(phoneHash, currentStep, tempData = {}) {
  const { data, error } = await getClient()
    .from('conversations')
    .upsert({
      phone_hash: phoneHash,
      current_step: currentStep,
      temp_data: tempData,
      last_updated: new Date().toISOString()
    }, { onConflict: 'phone_hash' })
    .select()
    .single();

  if (error) throw error;
  return data;
}

// ============================================
// ADMIN SESSIONS — Dashboard Authentication
// ============================================

const SESSION_MAX_AGE_MS = 24 * 60 * 60 * 1000; // 24 hours

/**
 * Create a new admin session
 */
async function createAdminSession(token) {
  const { error } = await getClient()
    .from('admin_sessions')
    .insert({ token, created_at: new Date().toISOString() });

  if (error) throw error;
}

/**
 * Validate an admin session token
 * Returns true if token exists and is less than 24 hours old
 */
async function validateAdminSession(token) {
  if (!token) return false;

  const { data, error } = await getClient()
    .from('admin_sessions')
    .select('created_at')
    .eq('token', token)
    .single();

  if (error || !data) return false;

  const age = Date.now() - new Date(data.created_at).getTime();
  if (age > SESSION_MAX_AGE_MS) {
    // Session expired — clean it up
    await deleteAdminSession(token);
    return false;
  }

  return true;
}

/**
 * Delete an admin session (logout or expiry cleanup)
 */
async function deleteAdminSession(token) {
  const { error } = await getClient()
    .from('admin_sessions')
    .delete()
    .eq('token', token);

  if (error) throw error;
}

/**
 * Remove all expired sessions (called by the reminder cron job as a bonus)
 */
async function cleanupExpiredSessions() {
  const cutoff = new Date(Date.now() - SESSION_MAX_AGE_MS).toISOString();
  const { error } = await getClient()
    .from('admin_sessions')
    .delete()
    .lt('created_at', cutoff);

  if (error) throw error;
}

// ============================================
// EXPORTS
// ============================================

module.exports = {
  // Date utilities
  ALL_TIME_SLOTS,
  getTodayIST,
  getDateOffsetIST,

  // Appointments
  createAppointment,
  getAppointmentsByDate,
  getAppointmentsRange,
  getAllAppointments,
  updateAppointmentStatus,
  checkSlotAvailable,
  getNextAvailableSlots,
  getUpcomingReminders,
  markReminderSent,

  // Stats
  getMonthlyStats,
  getTodayCount,

  // Conversations
  getConversation,
  upsertConversation,

  // Sessions
  createAdminSession,
  validateAdminSession,
  deleteAdminSession,
  cleanupExpiredSessions
};

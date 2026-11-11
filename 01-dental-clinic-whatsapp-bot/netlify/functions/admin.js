// ============================================
// ADMIN API — Dashboard Backend Routes
// Express app wrapped with serverless-http for Netlify Functions
// ============================================

const express = require('express');
const serverless = require('serverless-http');
const db = require('../../services/database');
const { generateSessionToken } = require('../../services/security');

const app = express();
app.use(express.json());

const router = express.Router();

// ============================================
// AUTH MIDDLEWARE
// ============================================

/**
 * Authenticate admin requests using Bearer token
 * Token is validated against the admin_sessions table in Supabase
 */
async function authenticate(req, res, next) {
  try {
    const authHeader = req.headers.authorization;
    if (!authHeader || !authHeader.startsWith('Bearer ')) {
      return res.status(401).json({ error: 'No authorization token provided' });
    }

    const token = authHeader.slice(7);
    const valid = await db.validateAdminSession(token);
    if (!valid) {
      return res.status(401).json({ error: 'Session expired or invalid' });
    }

    // Attach token to request for use in logout
    req.sessionToken = token;
    next();
  } catch (err) {
    console.error('Auth middleware error:', err.message);
    return res.status(500).json({ error: 'Authentication check failed' });
  }
}

// ============================================
// POST /login — Validate password, create session
// ============================================

router.post('/login', async (req, res) => {
  try {
    const { password } = req.body;

    if (!password) {
      return res.status(400).json({ error: 'Password is required' });
    }

    const adminPassword = process.env.ADMIN_PASSWORD;
    if (!adminPassword) {
      console.error('ADMIN_PASSWORD environment variable is not set');
      return res.status(500).json({ error: 'Server configuration error' });
    }

    if (password !== adminPassword) {
      return res.status(401).json({ error: 'Incorrect password' });
    }

    // Generate secure session token and store in Supabase
    const token = generateSessionToken();
    await db.createAdminSession(token);

    res.json({ token });
  } catch (err) {
    console.error('Login error:', err.message);
    res.status(500).json({ error: 'Login failed. Please try again.' });
  }
});

// ============================================
// GET /verify — Check if session is still valid
// ============================================

router.get('/verify', authenticate, (req, res) => {
  res.json({ valid: true });
});

// ============================================
// GET /appointments — Fetch appointments with filter
// Query param: filter = 'today' | 'upcoming' | 'all'
// ============================================

router.get('/appointments', authenticate, async (req, res) => {
  try {
    const { filter } = req.query;
    let appointments;

    if (filter === 'today') {
      const today = db.getTodayIST();
      appointments = await db.getAppointmentsByDate(today);
    } else if (filter === 'upcoming') {
      const today = db.getTodayIST();
      const weekLater = db.getDateOffsetIST(7);
      appointments = await db.getAppointmentsRange(today, weekLater);
    } else {
      // 'all' or no filter — return everything
      appointments = await db.getAllAppointments();
    }

    res.json(appointments);
  } catch (err) {
    console.error('Fetch appointments error:', err.message);
    res.status(500).json({ error: 'Failed to fetch appointments' });
  }
});

// ============================================
// PATCH /appointments/:id — Update appointment status
// Body: { status: 'confirmed' | 'completed' | 'cancelled' | 'no-show' }
// ============================================

router.patch('/appointments/:id', authenticate, async (req, res) => {
  try {
    const id = parseInt(req.params.id);
    const { status } = req.body;

    if (isNaN(id)) {
      return res.status(400).json({ error: 'Invalid appointment ID' });
    }

    if (!status) {
      return res.status(400).json({ error: 'Status is required' });
    }

    const validStatuses = ['pending', 'confirmed', 'completed', 'cancelled', 'no-show'];
    if (!validStatuses.includes(status)) {
      return res.status(400).json({
        error: `Invalid status. Must be one of: ${validStatuses.join(', ')}`
      });
    }

    const updated = await db.updateAppointmentStatus(id, status);
    res.json(updated);
  } catch (err) {
    console.error('Update status error:', err.message);
    res.status(500).json({ error: 'Failed to update appointment status' });
  }
});

// ============================================
// GET /stats — Monthly statistics
// Returns { todayCount, totalBookings, noShows }
// ============================================

router.get('/stats', authenticate, async (req, res) => {
  try {
    const [monthlyStats, todayCount] = await Promise.all([
      db.getMonthlyStats(),
      db.getTodayCount()
    ]);

    res.json({
      todayCount,
      totalBookings: monthlyStats.totalBookings,
      noShows: monthlyStats.noShows
    });
  } catch (err) {
    console.error('Stats error:', err.message);
    res.status(500).json({ error: 'Failed to fetch stats' });
  }
});

// ============================================
// POST /logout — Invalidate session
// ============================================

router.post('/logout', authenticate, async (req, res) => {
  try {
    await db.deleteAdminSession(req.sessionToken);
    res.json({ success: true });
  } catch (err) {
    console.error('Logout error:', err.message);
    res.status(500).json({ error: 'Logout failed' });
  }
});

// ============================================
// MOUNT ROUTER & EXPORT
// ============================================

// Mount router on all possible incoming base paths to ensure local dev and prod environments both match correctly.
app.use('/.netlify/functions/admin', router);
app.use('/api/admin', router);
app.use('/admin', router);
app.use('/', router); // Fallback for direct clean proxy calls

module.exports.handler = serverless(app);

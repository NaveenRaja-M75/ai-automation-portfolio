# Dental Clinic WhatsApp Bot

A WhatsApp appointment assistant for dental clinics, with a staff dashboard.

## Features

- **Booking flow over WhatsApp:** name → treatment → date → time → confirm, with slot-availability checks and suggested alternative slots
- **Cancellations and reminders:** patients cancel by message; a scheduled function sends reminders and handles replies
- **Urgent routing:** emergency and "talk to staff" options
- **Admin dashboard:** daily, monthly and upcoming bookings; staff update status (checked in, no-show)
- **Security:** Twilio request-signature verification, hashed phone numbers, password-protected admin API

## Structure

```
netlify/functions/webhook.js   Twilio WhatsApp webhook entry point
netlify/functions/admin.js     Admin dashboard API
netlify/functions/reminder.js  Scheduled reminder job
services/conversation.js       Conversation state machine
services/database.js           Supabase queries
services/security.js           Signature checks, hashing, auth
services/whatsapp.js           Outbound Twilio messages
public/                        Dashboard UI (HTML, CSS, JS)
```

## Run locally

```bash
npm install
cp .env.example .env   # fill in Twilio and Supabase values
npm run dev            # netlify dev
```

Stack: Node.js, Express, Twilio WhatsApp API, Supabase, Netlify Functions.

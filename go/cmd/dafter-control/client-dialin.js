const DIAL_IN_GUESTS = ['dial_in', 'both'];
const DIAL_OUT_GUESTS = ['dial_out', 'both'];
const NUMBER_CHECKS = ['pin_and_number', 'number'];
const ALLOWED_NUMBER_SEPARATORS = /[,;\n]+/;

function takesDialIn(guests) {
  return DIAL_IN_GUESTS.includes(guests);
}

function callsGuestsOut(guests) {
  return DIAL_OUT_GUESTS.includes(guests);
}

function telephonyOverride(guests) {
  const telephony = { phoneGuests: guests };
  if (takesDialIn(guests)) telephony.dialIn = { callerCheck: document.getElementById('dial-in-check').value || 'pin' };
  return telephony;
}

function renderDialInFields() {
  const guests = document.getElementById('phone-guests').value;
  const check = document.getElementById('dial-in-check').value;
  document.getElementById('dial-in-fields').style.display = takesDialIn(guests) ? '' : 'none';
  const agent = document.getElementById('agent-mode');
  if (guests && agent.value === 'off') agent.value = '';
  document.getElementById('dial-in-numbers-field').style.display = takesDialIn(guests) && NUMBER_CHECKS.includes(check) ? '' : 'none';
}

function allowedNumbersFrom(text) {
  const entries = String(text || '').split(ALLOWED_NUMBER_SEPARATORS).map((e) => e.trim()).filter(Boolean);
  const numbers = entries.map(phoneNumberFrom);
  const bad = numbers.map((n, i) => (n ? -1 : i)).filter((i) => i >= 0);
  return { numbers: numbers.filter(Boolean), bad };
}

async function allowDialInNumbers(data) {
  const telephony = (data.config && data.config.telephony) || {};
  const check = (telephony.dialIn && telephony.dialIn.callerCheck) || 'pin';
  const input = document.getElementById('dial-in-numbers');
  if (!takesDialIn(telephony.phoneGuests) || !NUMBER_CHECKS.includes(check)) return;
  const { numbers, bad } = allowedNumbersFrom(input.value);
  if (bad.length) {
    log(`Allowed numbers refused: entries ${bad.map((i) => i + 1).join(', ')} are not E.164 numbers, so none were sent`, 'error');
    return;
  }
  try {
    const resp = await fetch(`/sessions/${data.sessionId}/dial-in/numbers`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ numbers }),
    });
    const result = await resp.json();
    if (!resp.ok) {
      log(`Allowed numbers refused: ${result.code} - ${result.message}${result.details ? ` (${result.details.join('; ')})` : ''}`, 'error');
      return;
    }
    input.value = '';
    log(`${result.allowedNumbers} calling number(s) may dial in under the ${check} check`, 'success');
  } catch (err) {
    log(`Allowed numbers failed: ${err.message}`, 'error');
  }
}

function spacedPIN(pin) {
  return String(pin || '').replace(/(\d{4})(?=\d)/g, '$1 ');
}

function dialInText(details) {
  if (!details || !details.pin || !details.numbers || !details.numbers.length) return '';
  return `Dial in: ${details.numbers.join(' or ')} · PIN ${spacedPIN(details.pin)}`;
}

function showDialIn(details, guests) {
  const text = dialInText(details);
  const line = document.getElementById('guests-dial-in');
  line.textContent = text;
  line.style.display = text ? '' : 'none';
  document.getElementById('guests-dial-out').style.display = takesDialIn(guests) && !callsGuestsOut(guests) ? 'none' : '';
}

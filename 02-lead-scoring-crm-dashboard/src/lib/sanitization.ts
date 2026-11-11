const BLACKLIST_NAMES = [
  'looking',
  'searching',
  'interested',
  'property',
  'apartment',
  'flat',
  'bhk',
  'villa',
  'plot',
  'house',
  'studio',
  'hello',
  'hi',
  'hey',
  'customer',
  'user',
  'client',
  'buyer',
  'salesperson',
  'representative',
  'agent',
  'someone',
  'person',
  'planning',
  'ready',
  'occupy',
  'buy',
  'purchase',
  'need',
  'want',
  'require',
  'chennai',
  'here',
  'there',
  'this',
  'side',
];

export function sanitizeName(nameStr: string | null): string | null {
  if (!nameStr) return null;
  
  const clean = nameStr.replace(/[^A-Za-z\s]/g, '').trim();
  if (clean.length < 2) return null;

  const words = clean.split(/\s+/);
  
  // Brutal Honesty: Most names are 1-2 words. 3 is possible (First Middle Last).
  // If a name contains 4 or more words (e.g. "looking for a flat"), it is a sentence, not a name.
  if (words.length >= 4) return null;

  const lower = clean.toLowerCase();
  
  // Reject if name matches or contains any blacklisted word
  const isInvalid = BLACKLIST_NAMES.some(blacklistWord => {
    const regex = new RegExp(`\\b${blacklistWord}\\b`, 'i');
    return regex.test(lower);
  });

  if (isInvalid) return null;

  // Capitalize properly
  return words.map(w => w.charAt(0).toUpperCase() + w.slice(1).toLowerCase()).join(' ');
}

export function normalizeAreaName(areaStr: string | null): string | null {
  if (!areaStr) return null;
  
  // Clean prefix and suffix words
  const clean = areaStr.toLowerCase()
    .replace(/\b(near|around|in|at|location|area|road|street|place|side|stretch|off|region|hub|zone|side)\b/g, '')
    .replace(/\s+/g, ' ')
    .trim();
  
  if (!clean) return null;
  
  // Map common abbreviations and hubs to their proper standard names
  const mappings: Record<string, string> = {
    'omr': 'OMR',
    'ecr': 'ECR',
    'velachery': 'Velachery',
    'velachary': 'Velachery',
    'tambaram': 'Tambaram',
    'adyar': 'Adyar',
    'guindy': 'Guindy',
    'sholinganallur': 'Sholinganallur',
    'medavakkam': 'Medavakkam',
    'perungudi': 'Perungudi',
    'porur': 'Porur',
    'anna nagar': 'Anna Nagar',
    'annanagar': 'Anna Nagar',
    'whitefield': 'Whitefield',
    'hebbal': 'Hebbal',
  };
  
  for (const [key, val] of Object.entries(mappings)) {
    if (clean.includes(key)) {
      return val;
    }
  }
  
  // Capitalize first letter of each word as default fallback
  return clean.split(' ').map(w => w.charAt(0).toUpperCase() + w.slice(1).toLowerCase()).join(' ');
}

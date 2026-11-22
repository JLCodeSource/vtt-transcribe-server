import { fetchEngines } from './api';

/** Shown until (or if) the server's own list arrives: a short list of common languages. */
export const TRANSLATION_LANGUAGES = [
  'Arabic',
  'Chinese',
  'Dutch',
  'English',
  'French',
  'German',
  'Hindi',
  'Italian',
  'Japanese',
  'Korean',
  'Polish',
  'Portuguese',
  'Russian',
  'Spanish',
  'Turkish',
  'Ukrainian',
];

let cached: Promise<string[]> | null = null;

/** The languages the server's translation model supports (one request, shared by every dropdown). */
export function loadLanguages(): Promise<string[]> {
  cached ??= fetchEngines()
    .then((r) => (r.languages && r.languages.length > 0 ? r.languages : TRANSLATION_LANGUAGES))
    .catch(() => {
      cached = null; // try again next time
      return TRANSLATION_LANGUAGES;
    });
  return cached;
}

/** The list, plus a value saved earlier that is not in it (so an old free-text choice is not lost). */
export const languageChoices = (list: string[], current: string): string[] =>
  current && !list.includes(current) ? [...list, current] : list;

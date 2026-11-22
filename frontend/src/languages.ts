/** Languages offered for translation; the value is the language name the server passes to the translator. */
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

/** The list, plus a value saved earlier that is not in it (so an old free-text choice is not lost). */
export const languageChoices = (current: string): string[] =>
  current && !TRANSLATION_LANGUAGES.includes(current) ? [...TRANSLATION_LANGUAGES, current] : TRANSLATION_LANGUAGES;

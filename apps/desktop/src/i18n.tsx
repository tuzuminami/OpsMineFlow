import { createContext, useContext, useEffect, useMemo, useState } from "react";
import en from "./locales/en.json";
import ja from "./locales/ja.json";

export type Language = "ja" | "en";
export type TranslationKey = keyof typeof en;
type TranslationParams = Record<string, string | number>;

const dictionaries: Record<Language, Record<TranslationKey, string>> = { en, ja };
const STORAGE_KEY = "opsmineflow.language";

export function resolveInitialLanguage(saved: string | null, browserLanguage: string): Language {
  if (saved === "ja" || saved === "en") return saved;
  return browserLanguage.toLowerCase().startsWith("ja") ? "ja" : "en";
}

export function translate(language: Language, key: TranslationKey, params: TranslationParams = {}): string {
  return Object.entries(params).reduce(
    (message, [name, value]) => message.replaceAll(`{${name}}`, String(value)),
    dictionaries[language][key]
  );
}

type I18nValue = {
  language: Language;
  setLanguage: (language: Language) => void;
  t: (key: TranslationKey, params?: TranslationParams) => string;
  formatDateTime: (value: string) => string;
};

const I18nContext = createContext<I18nValue | null>(null);

export function I18nProvider({ children }: { children: React.ReactNode }) {
  const [language, setLanguage] = useState<Language>(() =>
    resolveInitialLanguage(window.localStorage.getItem(STORAGE_KEY), window.navigator.language)
  );

  useEffect(() => {
    window.localStorage.setItem(STORAGE_KEY, language);
    document.documentElement.lang = language;
  }, [language]);

  const value = useMemo<I18nValue>(
    () => ({
      language,
      setLanguage,
      t: (key, params) => translate(language, key, params),
      formatDateTime: (dateValue) => new Intl.DateTimeFormat(language === "ja" ? "ja-JP" : "en-US", {
        dateStyle: "medium",
        timeStyle: "short"
      }).format(new Date(dateValue))
    }),
    [language]
  );

  return <I18nContext.Provider value={value}>{children}</I18nContext.Provider>;
}

export function useI18n(): I18nValue {
  const value = useContext(I18nContext);
  if (!value) throw new Error("useI18n must be used inside I18nProvider");
  return value;
}

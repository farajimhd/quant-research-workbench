export const CONFIGURATION_SESSION_KEY = "trading-configuration-session-v2";
export const LEGACY_CONFIGURATION_SESSION_KEY = "trading-configuration-session-v1";
export const CONFIGURATION_SESSION_PAYLOAD_VERSION = 5;
export const CONFIGURATION_SESSION_CHANGED_EVENT = "quant-trading-configuration-session-changed";

export function readConfigurationSession<T>(): T | null {
  const current = window.sessionStorage.getItem(CONFIGURATION_SESSION_KEY);
  const legacy = window.sessionStorage.getItem(LEGACY_CONFIGURATION_SESSION_KEY);
  const stored = current ?? legacy;
  if (!stored) return null;
  const parsed = JSON.parse(stored);
  return (parsed?.payload_version === CONFIGURATION_SESSION_PAYLOAD_VERSION
    ? parsed.configuration
    : parsed) as T;
}

export function writeConfigurationSession(configuration: unknown) {
  window.sessionStorage.setItem(CONFIGURATION_SESSION_KEY, JSON.stringify({
    configuration,
    payload_version: CONFIGURATION_SESSION_PAYLOAD_VERSION,
  }));
  window.sessionStorage.removeItem(LEGACY_CONFIGURATION_SESSION_KEY);
  window.dispatchEvent(new CustomEvent(CONFIGURATION_SESSION_CHANGED_EVENT));
}

export function clearConfigurationSession() {
  window.sessionStorage.removeItem(CONFIGURATION_SESSION_KEY);
  window.sessionStorage.removeItem(LEGACY_CONFIGURATION_SESSION_KEY);
  window.dispatchEvent(new CustomEvent(CONFIGURATION_SESSION_CHANGED_EVENT));
}

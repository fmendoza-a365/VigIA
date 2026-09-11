export const API_BASE = import.meta.env.VITE_API_BASE_URL || window.location.origin;

export type AuthUser = {
  username: string;
  role: string;
};

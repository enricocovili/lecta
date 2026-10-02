/// <reference types="astro/client" />

declare namespace App {
  interface Locals {
    auth: { authenticated: boolean; username?: string; csrf?: string; needs_setup?: boolean };
  }
}

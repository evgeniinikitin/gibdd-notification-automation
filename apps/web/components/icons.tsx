import type { SVGProps } from "react";

export type IconName =
  | "admin"
  | "alert"
  | "arrow"
  | "back"
  | "balance"
  | "bus"
  | "calendar"
  | "check"
  | "chevron"
  | "clock"
  | "close"
  | "document"
  | "download"
  | "eye"
  | "file"
  | "filter"
  | "folder"
  | "grid"
  | "home"
  | "list"
  | "lock"
  | "menu"
  | "message"
  | "more"
  | "paperclip"
  | "plus"
  | "profile"
  | "refresh"
  | "route"
  | "search"
  | "send"
  | "settings"
  | "shield"
  | "signature"
  | "template"
  | "upload"
  | "users"
  | "wallet";

type IconProps = SVGProps<SVGSVGElement> & {
  name: IconName;
  size?: number;
};

function IconPaths({ name }: { name: IconName }) {
  switch (name) {
    case "home":
      return (
        <>
          <path d="m3 10 9-7 9 7" />
          <path d="M5 9v11h14V9M9 20v-6h6v6" />
        </>
      );
    case "message":
      return (
        <>
          <path d="M20 15a4 4 0 0 1-4 4H8l-5 3V7a4 4 0 0 1 4-4h9a4 4 0 0 1 4 4Z" />
          <path d="M8 9h8M8 13h5" />
        </>
      );
    case "document":
    case "file":
      return (
        <>
          <path d="M6 2h8l4 4v16H6z" />
          <path d="M14 2v5h4M9 12h6M9 16h6" />
        </>
      );
    case "folder":
      return <path d="M3 6h7l2 2h9v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2Z" />;
    case "profile":
      return (
        <>
          <circle cx="12" cy="8" r="4" />
          <path d="M4 21a8 8 0 0 1 16 0" />
        </>
      );
    case "admin":
    case "shield":
      return (
        <>
          <path d="M12 2 4.5 5v6c0 5.2 3.1 9.1 7.5 11 4.4-1.9 7.5-5.8 7.5-11V5z" />
          {name === "admin" ? <path d="M8.5 12h7M12 8.5v7" /> : <path d="m9 12 2 2 4-5" />}
        </>
      );
    case "settings":
      return (
        <>
          <circle cx="12" cy="12" r="3" />
          <path d="M19.4 15a2 2 0 0 0 .4 2.2l.1.1-2.6 2.6-.1-.1a2 2 0 0 0-2.2-.4 2 2 0 0 0-1.2 1.8V21h-3.6v-.2A2 2 0 0 0 9 19a2 2 0 0 0-2.2.4l-.1.1-2.6-2.6.1-.1A2 2 0 0 0 4.6 15a2 2 0 0 0-1.8-1.2H2.6v-3.6h.2A2 2 0 0 0 4.6 9a2 2 0 0 0-.4-2.2l-.1-.1 2.6-2.6.1.1A2 2 0 0 0 9 4.6a2 2 0 0 0 1.2-1.8v-.2h3.6v.2A2 2 0 0 0 15 4.6a2 2 0 0 0 2.2-.4l.1-.1 2.6 2.6-.1.1A2 2 0 0 0 19.4 9a2 2 0 0 0 1.8 1.2h.2v3.6h-.2a2 2 0 0 0-1.8 1.2Z" />
        </>
      );
    case "plus":
      return <path d="M12 5v14M5 12h14" />;
    case "search":
      return (
        <>
          <circle cx="11" cy="11" r="7" />
          <path d="m20 20-4-4" />
        </>
      );
    case "paperclip":
      return <path d="m21.4 11.6-8.9 8.9a6 6 0 0 1-8.5-8.5l9.4-9.4a4 4 0 0 1 5.7 5.7l-9.4 9.4a2 2 0 1 1-2.8-2.8l8.8-8.8" />;
    case "send":
      return (
        <>
          <path d="m22 2-7 20-4-9-9-4Z" />
          <path d="M22 2 11 13" />
        </>
      );
    case "check":
      return <path d="m5 12 4 4L19 6" />;
    case "alert":
      return (
        <>
          <path d="M10.3 3.6 2.7 17a2 2 0 0 0 1.7 3h15.2a2 2 0 0 0 1.7-3L13.7 3.6a2 2 0 0 0-3.4 0Z" />
          <path d="M12 9v4M12 17h.01" />
        </>
      );
    case "clock":
      return (
        <>
          <circle cx="12" cy="12" r="9" />
          <path d="M12 7v5l3 2" />
        </>
      );
    case "calendar":
      return (
        <>
          <rect x="3" y="5" width="18" height="16" rx="2" />
          <path d="M16 3v4M8 3v4M3 10h18" />
        </>
      );
    case "route":
      return (
        <>
          <circle cx="6" cy="18" r="2" />
          <circle cx="18" cy="6" r="2" />
          <path d="M8 18h2a2 2 0 0 0 2-2V8a2 2 0 0 1 2-2h2" />
        </>
      );
    case "users":
      return (
        <>
          <circle cx="9" cy="8" r="3" />
          <path d="M3 20a6 6 0 0 1 12 0M16 4.5a3 3 0 0 1 0 7M17 14a5 5 0 0 1 4 5" />
        </>
      );
    case "upload":
      return (
        <>
          <path d="M12 16V4M7 9l5-5 5 5" />
          <path d="M4 15v5h16v-5" />
        </>
      );
    case "download":
      return (
        <>
          <path d="M12 4v12M7 11l5 5 5-5" />
          <path d="M4 20h16" />
        </>
      );
    case "arrow":
      return <path d="M5 12h14M14 7l5 5-5 5" />;
    case "back":
      return <path d="M19 12H5M10 7l-5 5 5 5" />;
    case "chevron":
      return <path d="m9 18 6-6-6-6" />;
    case "menu":
      return <path d="M4 7h16M4 12h16M4 17h16" />;
    case "close":
      return <path d="m6 6 12 12M18 6 6 18" />;
    case "more":
      return (
        <>
          <circle cx="5" cy="12" r="1" fill="currentColor" stroke="none" />
          <circle cx="12" cy="12" r="1" fill="currentColor" stroke="none" />
          <circle cx="19" cy="12" r="1" fill="currentColor" stroke="none" />
        </>
      );
    case "list":
      return <path d="M9 6h11M9 12h11M9 18h11M4 6h.01M4 12h.01M4 18h.01" />;
    case "grid":
      return (
        <>
          <rect x="3" y="3" width="7" height="7" rx="1" />
          <rect x="14" y="3" width="7" height="7" rx="1" />
          <rect x="3" y="14" width="7" height="7" rx="1" />
          <rect x="14" y="14" width="7" height="7" rx="1" />
        </>
      );
    case "filter":
      return <path d="M4 5h16l-6 7v6l-4 2v-8Z" />;
    case "eye":
      return (
        <>
          <path d="M2 12s3.5-6 10-6 10 6 10 6-3.5 6-10 6S2 12 2 12Z" />
          <circle cx="12" cy="12" r="2.5" />
        </>
      );
    case "refresh":
      return (
        <>
          <path d="M20 7v5h-5" />
          <path d="M4 17v-5h5M19 12a7 7 0 0 0-12-5L4 10M5 12a7 7 0 0 0 12 5l3-3" />
        </>
      );
    case "lock":
      return (
        <>
          <rect x="4" y="10" width="16" height="11" rx="2" />
          <path d="M8 10V7a4 4 0 0 1 8 0v3M12 14v3" />
        </>
      );
    case "wallet":
    case "balance":
      return (
        <>
          <path d="M3 6h16a2 2 0 0 1 2 2v11H5a2 2 0 0 1-2-2Z" />
          <path d="M3 7V5a2 2 0 0 1 2-2h12M15 12h6M16 12h.01" />
        </>
      );
    case "bus":
      return (
        <>
          <rect x="5" y="2" width="14" height="18" rx="3" />
          <path d="M5 9h14M8 5h8M8 14h.01M16 14h.01M8 20v2M16 20v2" />
        </>
      );
    case "signature":
      return <path d="M3 17c4-7 6-11 8-11 3 0-2 10 1 10 2 0 3-5 5-5 1.5 0-.5 4 1 4 1 0 2-2 3-3M3 20h18" />;
    case "template":
      return (
        <>
          <rect x="3" y="3" width="18" height="18" rx="2" />
          <path d="M8 3v18M8 9h13M13 14h4M13 17h3" />
        </>
      );
  }
}

export function Icon({ name, size = 20, ...props }: IconProps) {
  return (
    <svg
      aria-hidden="true"
      fill="none"
      height={size}
      viewBox="0 0 24 24"
      width={size}
      stroke="currentColor"
      strokeLinecap="round"
      strokeLinejoin="round"
      strokeWidth={1.8}
      {...props}
    >
      <IconPaths name={name} />
    </svg>
  );
}

import type { ButtonHTMLAttributes, ReactNode } from "react";

type ButtonVariant = "default" | "destructive" | "ghost" | "primary" | "secondary";

type ButtonProps = ButtonHTMLAttributes<HTMLButtonElement> & {
  children?: ReactNode;
  iconOnly?: boolean;
  variant?: ButtonVariant;
};

export function Button({ children, className = "", iconOnly = false, variant = "default", ...props }: ButtonProps) {
  const classes = [
    iconOnly ? "icon-button" : "button",
    variant === "primary" ? "primary" : "",
    variant === "destructive" ? "danger" : "",
    variant === "ghost" ? "ghost" : "",
    variant === "secondary" ? "secondary" : "",
    className
  ]
    .filter(Boolean)
    .join(" ");
  return (
    <button className={classes} type="button" {...props}>
      {children}
    </button>
  );
}


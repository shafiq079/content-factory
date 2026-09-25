import './style.css';
export const metadata = { title: 'Content Factory', description: 'Local scene-based video production' };
export default function RootLayout({ children }: {children: React.ReactNode}) {
  return <html lang="en"><body>{children}</body></html>;
}

import './globals.css'
import type { Metadata } from 'next'

export const metadata: Metadata = {
  title: 'Xiao8',
  description: 'Your AI Assistant',
}

export default function RootLayout({
  children,
}: {
  children: React.ReactNode
}) {
  return (
    <html lang="en">
      <body className="min-h-screen bg-gray-100">
        {children}
      </body>
    </html>
  )
}
'use client';

import { useEffect, useState } from 'react';

interface Memory {
  id: string;
  content: string;
  timestamp: string;
  type: string;
}

export default function MemoryBrowserPage() {
  const [memories, setMemories] = useState<Memory[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    fetchMemories();
  }, []);

  const fetchMemories = async () => {
    try {
      const response = await fetch('/api/memories');
      const data = await response.json();
      setMemories(data);
    } catch (error) {
      console.error('Error fetching memories:', error);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="container mx-auto p-4">
      <h1 className="text-2xl font-bold mb-4">记忆浏览器</h1>
      
      {loading ? (
        <div>加载中...</div>
      ) : (
        <div className="grid gap-4">
          {memories.map((memory) => (
            <div 
              key={memory.id}
              className="bg-white p-4 rounded-lg shadow"
            >
              <div className="flex justify-between items-start mb-2">
                <span className="text-sm text-gray-500">{memory.timestamp}</span>
                <span className="px-2 py-1 bg-blue-100 text-blue-800 rounded-full text-sm">
                  {memory.type}
                </span>
              </div>
              <p className="text-gray-700">{memory.content}</p>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

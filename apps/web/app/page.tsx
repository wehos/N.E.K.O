'use client';

import { useEffect, useRef, useState } from 'react';

export default function HomePage() {
  const live2dContainerRef = useRef<HTMLDivElement>(null);
  const live2dCanvasRef = useRef<HTMLCanvasElement>(null);
  const [mounted, setMounted] = useState(false);

  useEffect(() => {
    setMounted(true);
  }, []);

  if (!mounted) {
    return null;
  }

  return (
    <div className="relative h-screen w-screen bg-gray-100">
      {/* Live2D 容器 */}
      <div 
        ref={live2dContainerRef}
        className="absolute inset-0 bg-white/50"
        style={{
          border: '2px solid red' // 临时添加边框以便查看容器位置
        }}
      >
        <canvas 
          ref={live2dCanvasRef}
          width={800}
          height={600}
          className="absolute right-0 bottom-0"
          style={{
            border: '2px solid blue' // 临时添加边框以便查看画布位置
          }}
        />
      </div>

      {/* 聊天容器 */}
      <div 
        className="absolute left-5 bottom-5 w-[340px] h-[300px] bg-white shadow-xl rounded-xl"
        style={{
          border: '2px solid green' // 临时添加边框以便查看聊天容器位置
        }}
      >
        <div className="h-full p-4 overflow-y-auto">
          <div className="space-y-4">
            <div className="bg-blue-500 text-white p-4 rounded-lg max-w-[80%]">
              <p className="text-lg">你好！我是小八，有什么可以帮助你的吗？</p>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
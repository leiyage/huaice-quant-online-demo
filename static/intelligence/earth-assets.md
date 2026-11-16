# 地球娱乐纹理素材

`earth-land.js` 来自 Natural Earth 的 1:110m land 数据集，127 个多边形、5143 个顶点，保留内环、日期变更线和极点边界；经纬度舍入至小数点后四位。导出为 JS 常量，随模块离线加载，不增加运行依赖或远程请求。

- 来源：https://github.com/nvkelso/natural-earth-vector/blob/master/geojson/ne_110m_land.geojson
- 原始资源：https://raw.githubusercontent.com/nvkelso/natural-earth-vector/master/geojson/ne_110m_land.geojson
- 获取日期：2026-10-15
- 获取文本 SHA-256：`9e0729ee253ca7d7a5c4ae9395fb1902264c5377c52e224d13dd85010e2835d9`
- 授权：Natural Earth 地图矢量与栅格数据为 public domain。https://www.naturalearthdata.com/about/terms-of-use/

仅绘制陆地/海岸线，不展示主权国界。数据本身是全球小比例尺概化轮廓；深绿地表、数字点阵、字符、光流、亮度均为娱乐美术效果，非卫星影像、非地形测量、非真实城市夜光或金融关系。二维兼容地图继续沿用原来的示意图。

`earth-texture.js` 在初始化时生成一张 2048×1024 纹理；神经网、地球交互和业务数据仍各自独立。无需安装依赖，运行时无外网素材依赖。

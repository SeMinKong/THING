from setuptools import find_packages, setup

package_name = 'thing_logger'

setup(
    name=package_name,
    version='0.1.0',
    packages=find_packages(exclude=['test']),
    data_files=[
        ('share/ament_index/resource_index/packages',
            ['resource/' + package_name]),
        ('share/' + package_name, ['package.xml']),
    ],
    # requests는 격리 uploader 데몬(thing_logger.uploader)의 런타임 의존성이다.
    # logger 노드는 사용하지 않으며 uploader에서 lazy import한다.
    install_requires=['setuptools', 'requests'],
    zip_safe=True,
    maintainer='C103 Team',
    maintainer_email='dndwlqor@naver.com',
    description='rosbag2 recording and canonical session file export.',
    license='Apache-2.0',
    extras_require={
        'test': [
            'pytest',
        ],
    },
    entry_points={
        'console_scripts': [
            'logger = thing_logger.logger:main',
            # 격리 uploader 데몬(별도 프로세스, 소켓 서버). 실행 이름은 정민 님
            # 컨테이너 실행 명령과 정합 필요(S15P11C103-130).
            'uploader = thing_logger.uploader:main',
        ],
    },
)

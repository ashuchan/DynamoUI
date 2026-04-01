# DynamoUI

## Overview
DynamoUI is an intuitive user interface framework that enables developers to create dynamic and responsive applications with ease. The framework offers a variety of components that simplify the development process, allowing for rapid prototyping and production-ready applications.

## Architecture
DynamoUI follows a modular architecture, promoting separation of concerns and reusability. Key components include:
- **Core Module**: Contains the core functionalities and utilities required by all other modules.
- **Component Library**: A collection of UI components such as buttons, forms, and modals that can be easily integrated into applications.
- **Routing**: Built-in routing to manage navigation within applications seamlessly.

## Setup
To get started with DynamoUI, follow these steps:
1. Clone the repository:
   ```
   git clone https://github.com/ashuchan/DynamoUI.git
   cd DynamoUI
   ```
2. Start a virtual environment:
   ```
   python -m venv venv
   ```
3. Activate the virtual environment:
   ```
   .\venv\Scripts\activate
   ```
4. Upgrade python
   ```
   python -m pip install --upgrade pip setuptools wheel
   ```
5. Install project dependencies
   ```
   pip install -e ".[dev]"
   ```
6. Test the build
   ```
   python -m pytest --cov
   ```

## Features
- **Responsive Design**: Components are designed to adapt to various screen sizes.
- **Customizable**: Extensive theming capabilities to match your brand.
- **Accessibility**: Built with accessibility in mind, ensuring that applications are usable by everyone.

## Tech Stack
DynamoUI utilizes the following technologies:
- **React**: A JavaScript library for building user interfaces.
- **Redux**: For state management.
- **Webpack**: Module bundler for modern JavaScript applications.
- **Sass**: For styling the components.

## License
This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.